"""nyp_prepare_payout — build the payout instruction list for one month.

Thin wrapper around closer.sources.loader.load_period and
closer.rules.commission.compute_month.  Returns a list of transfer
instructions ready to hand to payroll — one row per eligible staff member
with a non-zero net payout.

Orders in open_disputes are held and split_overrides are applied, the same
way as in nyp_compute_commission. This only drafts the list; it pays nobody.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._payout import build_payout


@tool(
    name="nyp_prepare_payout",
    description=(
        "Build the final payout instruction list for one month: one row per "
        "eligible staff member with a non-zero net commission (after clawbacks). "
        "Returns staff name, ID, and amount in both dollars and cents. "
        "Held orders (open disputes, undelivered, unpaid) are excluded. "
        "Refuses while any bank credit is not in approved_matches, so pass "
        "every match the user confirmed."
    ),
)
def nyp_prepare_payout(
    month: str,
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Produce payout instructions for *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
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
        month, payouts (list of {staff_id, name, amount_dollars, amount_cents}),
        total_dollars, total_cents, clawbacks, clawback_total_dollars,
        hold_count, held_orders.
    """
    return build_payout(month, approved_matches, open_disputes, split_overrides, data_root)
