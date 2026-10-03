"""Golden-month score: run the rules engine on the golden orders and compare with the answer key.

Agents get scored here too as they land (duplicates found, matches, exceptions raised).
"""
from __future__ import annotations

import json
from pathlib import Path

from closer.data.models import Order
from closer.data.staff import load_staff
from closer.rules.commission import compute_month
from closer.rules.plan import load_plans


def load_key(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def score_commission(key: dict) -> dict:
    orders = [Order(**o) for o in key["golden_orders"]]
    result = compute_month(orders, load_plans(), load_staff(), key["month"], key["prior_payouts"])
    got = {k: v for k, v in result.totals().items() if v}
    want = key["commission_totals_cents"]
    people = sorted(set(got) | set(want))
    mismatches = {p: {"engine": got.get(p, 0), "key": want.get(p, 0)}
                  for p in people if got.get(p, 0) != want.get(p, 0)}
    return {"check": "commission_totals", "passed": not mismatches, "people": len(people),
            "mismatches": mismatches, "holds": len(result.holds), "lines": len(result.lines)}
