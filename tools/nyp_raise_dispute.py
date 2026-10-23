"""nyp_raise_dispute — open a commission dispute on an order.

Thin wrapper around tools._state.raise_dispute. Nothing is saved: the
Orchestrate tool runtime is read-only, so the agent carries the order_id in
open_disputes on later calls.
"""
from __future__ import annotations

from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._payout import build_payout
from tools._state import raise_dispute


@tool(
    name="nyp_raise_dispute",
    description=(
        "Open a commission dispute on a specific order. Nothing is saved. "
        "Pass month and the current approved_matches, open_disputes and "
        "split_overrides; it returns totals_by_name with this order held, to "
        "show exactly as returned. Keep the order_id in open_disputes on every "
        "later call. Only a sales manager can close it with nyp_record_ruling."
    ),
)
def nyp_raise_dispute(
    order_id: str,
    raised_by: str,
    claim: str,
    month: Optional[str] = None,
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Open a commission dispute for *order_id*.

    Parameters
    ----------
    order_id:
        The order being disputed, e.g. ``"1043"``.
    raised_by:
        Staff ID of the person raising the dispute, e.g. ``"S04"``.
    claim:
        Free-text description of the claim, e.g.
        ``"I closed this sale jointly with S05 — split should be 50/50."``.
    month, approved_matches, open_disputes, split_overrides:
        The close state so far. When month is given, the totals are
        recalculated with this order held.
    data_root:
        Override the default ``data/`` directory (useful in tests).

    Returns
    -------
    dict
        order_id, raised_by, claim, raised_at, status, next_step and, when
        month is given, open_disputes, totals_by_name and hold_count.
    """
    record = raise_dispute(order_id, raised_by, claim)
    if month:
        disputes = list(dict.fromkeys([*(open_disputes or []), order_id]))
        payout = build_payout(month, approved_matches, disputes, split_overrides, data_root)
        record["open_disputes"] = disputes
        record["totals_by_name"] = {
            f"{p['name']} ({p['staff_id']})": f"S${p['amount_dollars']}"
            for p in payout["payouts"]
        }
        record["hold_count"] = payout["hold_count"]
        record["next_step"] = (
            f"Show totals_by_name exactly as returned. Keep '{order_id}' in "
            "open_disputes on every later call. Only a sales manager can rule on it."
        )
    return record
