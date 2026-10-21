"""nyp_explain_statement — break down the commission statement for one staff member.

Thin wrapper around closer.sources.loader.load_period and
closer.rules.commission.compute_month.  Returns human-readable line items so
a salesperson can verify their own commission.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from closer.data.staff import load_staff
from closer.rules.commission import compute_month
from closer.rules.plan import load_plans
from closer.sources.loader import load_period
from tools._state import apply_split_overrides, inputs_used as _inputs_used

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def _data_dir(month: str, data_root: Path | None = None) -> Path:
    root = data_root or _DATA_ROOT
    candidate = root / month
    if candidate.is_dir():
        return candidate
    fallback = root / "golden"
    if fallback.is_dir():
        return fallback
    raise FileNotFoundError(
        f"No data directory found for month '{month}'. "
        f"Tried {candidate} and {fallback}."
    )


@tool(
    name="nyp_explain_statement",
    description=(
        "Show the full commission breakdown for one staff member: every earning "
        "line (commission, promo bonus, clawback) and every held order with the "
        "reason it was held. Use this when a salesperson queries their statement."
    ),
)
def nyp_explain_statement(
    month: str,
    staff_id: str,
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Return a personalised commission statement for *staff_id* in *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    staff_id:
        The staff member whose statement to explain, e.g. ``"S04"``.
    approved_matches:
        Bank line-number (string) → order_id, from nyp_match_payments.
    open_disputes:
        Order IDs with an open dispute (from nyp_raise_dispute); held.
    split_overrides:
        order_id -> split from nyp_record_ruling, e.g.
        ``{"1043": {"S04": 50, "S05": 50}}``.
    data_root:
        Override the default ``data/`` directory (useful in tests).

    Returns
    -------
    dict
        staff_id, month, total_dollars, total_cents,
        lines (filtered to this staff member),
        held_orders (orders that touched this staff member but earned nothing).
    """
    root = Path(data_root) if data_root else None
    approved: dict[int, str] = (
        {int(k): v for k, v in approved_matches.items()} if approved_matches else {}
    )

    period = load_period(month, _data_dir(month, root), approved)
    held = set(open_disputes or [])

    patched_orders = apply_split_overrides(period.orders, split_overrides, open_disputes)

    all_staff = load_staff()
    member = next((s for s in all_staff if s.staff_id == staff_id), None)
    if member is None:
        raise ValueError(f"Unknown staff_id '{staff_id}'.")

    result = compute_month(
        patched_orders,
        load_plans(),
        all_staff,
        month,
        period.prior_payouts,
        open_disputes=held if held else None,
    )

    my_lines = [line for line in result.lines if line.staff_id == staff_id]
    total_cents = sum(line.amount_cents for line in my_lines)

    # Holds relevant to this staff member: orders where they appear in the split
    # or are recorded as the salesperson.
    order_map = {o.order_id: o for o in patched_orders}

    def _involves(order_id: str) -> bool:
        o = order_map.get(order_id)
        return o is not None and (staff_id in o.split or o.salesperson == staff_id)

    my_holds = [
        {"order_id": h.order_id, "reason": h.reason}
        for h in result.holds
        if _involves(h.order_id)
    ]

    return {
        "staff_id": staff_id,
        "name": member.name,
        "month": month,
        "total_dollars": f"{total_cents / 100:,.2f}",
        "total_cents": total_cents,
        "lines": [
            {
                "order_id": line.order_id,
                "kind": line.kind,
                "amount_dollars": f"{line.amount_cents / 100:,.2f}",
                "amount_cents": line.amount_cents,
                "note": line.note,
            }
            for line in my_lines
        ],
        "held_orders": my_holds,
        "inputs_used": _inputs_used(approved_matches, open_disputes, split_overrides),
    }
