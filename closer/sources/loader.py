"""Load one month from the agent-visible files into golden orders. No LLM, no answer key.

Reads only what the shop actually has at month end:
  pos_orders.json      till records (no payment, delivery or refund truth)
  roadshow.csv         the roadshow sheet, including rows also keyed into the till
  bank.csv             the bank statement
  settlements.csv      the card terminal and Atome reports behind each batched credit
  crew_messages.json   the delivery crew's chat ("#1002 done", "#1052 fail ...")
  august_payouts.json  commission already paid out last month

How each fact is worked out:
  paid      every payment found for the order: bank lines referencing ORD<id>, card
            transactions in settlements.csv, an Atome FULL row (the order total), and
            for orders from an earlier month the till deposit, which was banked then.
            paid_on is the day the running total first covers the order.
  delivered the last crew message naming the order: done / delivered / ok / complete
            sets the day, fail sets None. No message means not delivered.
  refunded  a REFUND bank line referencing the order.
  roadshow  a row also keyed into the till (same phone and amount) is skipped. Other
            rows are linked to their ORDRS-NN reference by deposit amount and date, or
            for Atome by the order total; the sheet's row number is not the NN.

Bank credits with no usable reference (e.g. "SOFA LIM") are never guessed silently:
suggest_matches() proposes candidates and load_period() applies only the ones passed
in as approved.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from closer.data.catalog import CATALOG
from closer.data.models import LineItem, Order

AGENT_FILES = ("pos_orders.json", "roadshow.csv", "bank.csv", "settlements.csv",
               "crew_messages.json", "august_payouts.json")
_ORDER_REF = re.compile(r"^ORD(RS-\d+|\d+)(?:-REF|-INST)?$")
_CREW_ORDER = re.compile(r"(?:#|order\s+|^)(RS-\d+|\d{4})\b", re.IGNORECASE)
_CREW_FAIL = re.compile(r"\bfail", re.IGNORECASE)
_CREW_DONE = re.compile(r"\b(done|delivered|ok|complete)\b", re.IGNORECASE)
_ITEM = re.compile(r"^(\d+)x\s+(.+)$")
_PRODUCTS = {name.lower(): (sku, name, category, brand, cents)
             for sku, name, category, brand, cents in CATALOG}


@dataclass(frozen=True)
class BankLine:
    """One bank.csv row, numbered from 1 in file order."""

    line_no: int
    day: date
    description: str
    reference: str
    debit_cents: int
    credit_cents: int


@dataclass(frozen=True)
class MatchSuggestion:
    """An unreferenced bank credit and the orders it could pay, for a human to confirm."""

    line_no: int
    day: date
    description: str
    credit_cents: int
    candidates: tuple[str, ...]
    reason: str


@dataclass
class Period:
    month: str
    orders: list[Order]
    prior_payouts: dict[str, dict[str, int]]
    bank_lines: list[BankLine]
    unmatched_lines: list[BankLine]
    skipped_duplicates: list[dict] = field(default_factory=list)


def cents(text: str) -> int:
    """'1538.46' -> 153846, without floats."""
    text = (text or "0").strip().replace(",", "")
    whole, _, frac = text.partition(".")
    sign = -1 if whole.startswith("-") else 1
    return sign * (abs(int(whole or 0)) * 100 + int((frac + "00")[:2]))


def _order_ref(reference: str) -> str | None:
    match = _ORDER_REF.match(reference.strip().upper())
    return match.group(1) if match else None


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path):
    return json.loads(path.read_text("utf-8"))


def _bank_lines(rows: list[dict]) -> list[BankLine]:
    return [BankLine(n, date.fromisoformat(r["date"]), r["description"], r["reference"],
                     cents(r["debit"]), cents(r["credit"]))
            for n, r in enumerate(rows, start=1)]


def _deliveries(messages: list[dict]) -> dict[str, date | None]:
    """order_id -> delivery day (None after a failed attempt); the latest message wins."""
    out: dict[str, date | None] = {}
    for msg in sorted(messages, key=lambda m: m["sent_at"]):
        found = _CREW_ORDER.search(msg["text"])
        if not found:
            continue
        oid = found.group(1).upper()
        if _CREW_FAIL.search(msg["text"]):
            out[oid] = None
        elif _CREW_DONE.search(msg["text"]):
            out[oid] = datetime.fromisoformat(msg["sent_at"]).date()
    return out


def _items(text: str) -> tuple[LineItem, ...]:
    items = []
    for part in text.split(","):
        found = _ITEM.match(part.strip())
        if not found:
            continue
        sku, name, category, brand, unit = _PRODUCTS.get(
            found.group(2).strip().lower(), ("UNKNOWN", found.group(2).strip(), "unknown", "unknown", 0))
        items.append(LineItem(sku=sku, name=name, category=category, brand=brand,
                              qty=int(found.group(1)), unit_cents=unit))
    return tuple(items)


def _payments(bank: list[BankLine], settlements: list[dict],
              approved: Mapping[int, str]) -> tuple[dict[str, list[tuple[date, int]]], dict[str, date], dict[str, str]]:
    """order_id -> [(day, cents)], order_id -> refund day, order_id -> method seen."""
    paid: dict[str, list[tuple[date, int]]] = defaultdict(list)
    refunds: dict[str, date] = {}
    method: dict[str, str] = {}
    for line in bank:
        oid = _order_ref(line.reference) or approved.get(line.line_no)
        if oid is None or line.reference.upper().endswith("-INST"):
            continue  # Atome payouts are counted from settlements.csv at the order total
        if line.debit_cents and "REFUND" in line.description.upper():
            refunds[oid] = line.day
        elif line.credit_cents:
            paid[oid].append((line.day, line.credit_cents))
    for row in settlements:
        oid = _order_ref(row["reference"])
        if oid is None:
            continue
        paid[oid].append((date.fromisoformat(row["txn_date"]), cents(row["gross"])))
        method.setdefault(oid, "Atome" if row["provider"] == "ATOME" else row["provider"].title())
    return paid, refunds, method


def _paid_on(payments: list[tuple[date, int]], net: int, opening: int = 0) -> tuple[int, date | None]:
    total, day = opening, None
    for when, amount in sorted(payments):
        total += amount
        if day is None and total >= net:
            day = when
    return total, day


def _link_roadshow(rows: list[dict], pos_keys: set[tuple[str, int]],
                   bank: list[BankLine], settlements: list[dict]) -> tuple[list[tuple[dict, str]], list[dict]]:
    """[(row, order_id)] for roadshow-only rows, plus the rows skipped as till duplicates."""
    deposits: dict[tuple[int, date], str] = {}
    for line in bank:
        oid = _order_ref(line.reference)
        if oid and oid.startswith("RS-") and line.description.upper().endswith("DEP"):
            deposits.setdefault((line.credit_cents, line.day), oid)
    atome: dict[int, list[str]] = defaultdict(list)
    for row in settlements:
        oid = _order_ref(row["reference"])
        if not oid or not oid.startswith("RS-"):
            continue
        if row["description"] == "DEP":
            deposits.setdefault((cents(row["gross"]), date.fromisoformat(row["txn_date"])), oid)
        elif row["provider"] == "ATOME":
            atome[cents(row["gross"])].append(oid)

    linked, duplicates = [], []
    for row in rows:
        amount = cents(row["amount"])
        if (row["phone"].strip(), amount) in pos_keys:
            duplicates.append(row)
            continue
        oid = deposits.get((cents(row["deposit"]), date.fromisoformat(row["date"])))
        if oid is None and len(atome.get(amount, [])) == 1:
            oid = atome[amount][0]
        linked.append((row, oid or f"RS-ROW{int(row['row']):02d}"))
    return linked, duplicates


def load_period(month: str, data_dir: Path, approved_matches: Mapping[int, str] | None = None) -> Period:
    """Every order the close must look at, built only from the agent-visible files.

    approved_matches: bank line number -> order_id, for unreferenced credits a person
    has confirmed (see suggest_matches).
    """
    approved = dict(approved_matches or {})
    till = _read_json(data_dir / "pos_orders.json")
    sheet = _read_csv(data_dir / "roadshow.csv")
    bank = _bank_lines(_read_csv(data_dir / "bank.csv"))
    settlements = _read_csv(data_dir / "settlements.csv")
    delivered = _deliveries(_read_json(data_dir / "crew_messages.json"))
    prior_payouts: dict[str, dict[str, int]] = defaultdict(dict)
    for p in _read_json(data_dir / "august_payouts.json"):
        prior_payouts[str(p["order_id"])][str(p["staff_id"])] = int(p["amount_cents"])

    paid, refunds, seen_method = _payments(bank, settlements, approved)
    orders: list[Order] = []

    for rec in till:
        oid = str(rec["order_id"])
        net = int(rec["net_total_cents"])
        created = date.fromisoformat(rec["created_at"])
        earlier = created.strftime("%Y-%m") < month
        # A deposit taken at the till in an earlier month sits on that month's statement.
        opening = int(rec["deposit_cents"]) if earlier and rec["payment_method"] != "Atome" else 0
        total, paid_on = _paid_on(paid.get(oid, []), net, opening)
        delivered_on = delivered.get(oid)
        if oid in prior_payouts:
            # Commission was paid out on it last month, so it was fully paid and delivered then.
            total, paid_on = net, paid_on or delivered_on or created
        orders.append(Order(
            order_id=oid, source="pos", created_on=created, customer_name=rec["customer_name"],
            phone=str(rec["phone"]), items=tuple(LineItem(**i) for i in rec["items"]),
            list_total_cents=int(rec["list_total_cents"]), discount_pct=int(rec["discount_pct"]),
            discount_approved_by=rec["discount_approved_by"], net_total_cents=net,
            salesperson=rec["salesperson"], split=rec["split"] or {},
            payment_method=rec["payment_method"], paid_cents=min(total, net) if paid_on else total,
            paid_on=paid_on, delivered_on=delivered_on, refunded_on=refunds.get(oid),
        ))

    pos_keys = {(o.phone, o.net_total_cents) for o in orders}
    month_rows = [r for r in sheet if r["date"].startswith(month)]
    linked, duplicates = _link_roadshow(month_rows, pos_keys, bank, settlements)
    for row, oid in linked:
        net = cents(row["amount"])
        total, paid_on = _paid_on(paid.get(oid, []), net)
        salesperson = row["salesperson"].strip() or None
        orders.append(Order(
            order_id=oid, source="roadshow", created_on=date.fromisoformat(row["date"]),
            customer_name=row["customer_name"], phone=row["phone"].strip(), items=_items(row["items_text"]),
            list_total_cents=net, discount_pct=0, discount_approved_by=None, net_total_cents=net,
            salesperson=salesperson, split={salesperson: 100} if salesperson else {},
            payment_method=seen_method.get(oid, "bank transfer"),
            paid_cents=min(total, net) if paid_on else total, paid_on=paid_on,
            delivered_on=delivered.get(oid), refunded_on=refunds.get(oid),
        ))

    used = {line.line_no for line in bank if _order_ref(line.reference)} | set(approved)
    unmatched = [line for line in bank
                 if line.credit_cents and line.line_no not in used and not line.reference.startswith("CSETL")]
    return Period(month=month, orders=orders, prior_payouts=dict(prior_payouts), bank_lines=bank,
                  unmatched_lines=unmatched, skipped_duplicates=duplicates)


def suggest_matches(period: Period) -> list[MatchSuggestion]:
    """For each unreferenced credit, the orders whose deposit or outstanding balance it equals.

    A deposit candidate is an order created that day whose 30% deposit is the amount;
    a balance candidate is an order still short by exactly the amount. Nothing is applied:
    pass the confirmed ones to load_period(approved_matches=...).
    """
    out = []
    for line in period.unmatched_lines:
        candidates, reasons = [], []
        for o in period.orders:
            if o.paid_on is not None:
                continue
            if o.created_on == line.day and o.net_total_cents * 30 // 100 == line.credit_cents \
                    and o.paid_cents == 0:
                candidates.append(o.order_id)
                reasons.append(f"{o.order_id}: deposit on the order day")
            elif o.net_total_cents - (o.paid_cents or o.net_total_cents * 30 // 100) == line.credit_cents \
                    and line.day >= o.created_on:
                candidates.append(o.order_id)
                reasons.append(f"{o.order_id}: clears the balance")
        out.append(MatchSuggestion(line.line_no, line.day, line.description or "(blank)",
                                   line.credit_cents, tuple(candidates), "; ".join(reasons) or "no candidate"))
    return out
