"""nyp_record_ruling — close a dispute and record the manager's ruling.

Thin wrapper around tools._state.record_ruling.  Only a staff member whose
role is sales_manager may call this; the check is enforced inside _state and
raises PermissionError for any other caller. Nothing is saved; the agent
passes the returned split in split_overrides on later calls.
"""
from __future__ import annotations

from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._payout import build_payout
from tools._state import record_ruling


@tool(
    name="nyp_record_ruling",
    description=(
        "Close an open dispute and record the sales manager's ruling. "
        "Only a staff member with the sales_manager role may "
        "call this tool — any other caller receives a PermissionError. "
        "The order must be in open_disputes. Pass the current approved_matches "
        "and split_overrides too; it returns totals_by_name with the ruling "
        "applied, to show exactly as returned. Nothing is saved: afterwards, "
        "use the returned open_disputes and split_overrides on later calls."
    ),
)
def nyp_record_ruling(
    order_id: str,
    ruled_by: str,
    ruling: str,
    split: Optional[dict[str, float]] = None,
    open_disputes: Optional[list[str]] = None,
    month: str = "2026-09",
    approved_matches: Optional[dict[str, str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Record the manager's ruling on a dispute and optionally override the split.

    Parameters
    ----------
    order_id:
        The disputed order, e.g. ``"1043"``.
    ruled_by:
        Staff ID of the ruling manager, e.g. ``"S02"``.
        Must have role ``sales_manager`` in the staff roster; otherwise a
        ``PermissionError`` is raised naming who is authorised.
    ruling:
        Free-text ruling, e.g.
        ``"Ravi assisted but Aisyah led the sale — split 60/40."``
    split:
        Optional new split for the order, e.g. ``{"S05": 60, "S04": 40}``.
        Whole percentages that sum to 100 (fractions like 0.5 are
        converted). Pass ``null`` / omit to leave the original split.
    open_disputes:
        Order IDs with an open dispute; must include *order_id*.

    Returns
    -------
    dict
        order_id, ruled_by, ruling, split, ruled_at, status, next_step and,
        once every bank line is confirmed, open_disputes, split_overrides,
        totals_by_name and hold_count with the ruling applied.
    """
    record = record_ruling(order_id, ruled_by, ruling, split, open_disputes)
    disputes = [d for d in (open_disputes or []) if d != order_id]
    overrides = {k: v for k, v in (split_overrides or {}).items() if k != order_id}
    if record["split"] is not None:
        overrides[order_id] = record["split"]
    try:
        payout = build_payout(month, approved_matches, disputes, overrides or None, data_root)
    except (ValueError, FileNotFoundError):
        # Bank lines not confirmed yet, or no data for that month: the
        # ruling still stands; the totals come from nyp_compute_commission.
        payout = None
    if payout:
        record["open_disputes"] = disputes
        record["split_overrides"] = overrides
        record["totals_by_name"] = {
            f"{p['name']} ({p['staff_id']})": f"S${p['amount_dollars']}"
            for p in payout["payouts"]
        }
        record["hold_count"] = payout["hold_count"]
        record["next_step"] = (
            "Show totals_by_name exactly as returned. Use the returned "
            "open_disputes and split_overrides on every later call."
        )
    return record
