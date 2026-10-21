"""nyp_approve_payout — the owner approves or rejects the month's payout draft.

Rebuilds the draft from the same close state as nyp_prepare_payout, so the
owner decides on exactly those figures, then returns a who/what/when record.
Only the owner (role ``owner`` in the staff roster) may decide. Nothing is
stored and no money moves: the agent keeps the returned log_entry in the
conversation.
"""
from __future__ import annotations

from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._state import record_payout_decision
from tools._payout import build_payout


@tool(
    name="nyp_approve_payout",
    description=(
        "Record the owner's decision on the month's payout draft: 'approve' or "
        "'reject'. Only the owner may decide; anyone else gets a PermissionError. "
        "Pass the same approved_matches, open_disputes and split_overrides as "
        "nyp_prepare_payout. Returns who decided, what, when and the totals. "
        "Nothing is paid by this tool."
    ),
)
def nyp_approve_payout(
    month: str,
    decided_by: str,
    decision: str,
    reason: str = "",
    approved_matches: Optional[dict[str, str]] = None,
    open_disputes: Optional[list[str]] = None,
    split_overrides: Optional[dict[str, dict[str, float]]] = None,
    data_root: Optional[str] = None,
) -> dict:
    """Approve or reject the payout draft for *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    decided_by:
        Staff ID the person gave for themselves, e.g. ``"S01"``.
        Must have role ``owner``.
    decision:
        ``"approve"`` or ``"reject"``.
    reason:
        Optional free text from the owner.
    approved_matches, open_disputes, split_overrides:
        The close state, exactly as passed to nyp_prepare_payout.
    data_root:
        Override the default ``data/`` directory (useful in tests).

    Returns
    -------
    dict
        month, decision, decided_by, decided_at, reason, total_dollars,
        total_cents, payouts, hold_count, status, log_entry.
    """
    payout = build_payout(month, approved_matches, open_disputes, split_overrides, data_root)
    return record_payout_decision(month, decided_by, decision, reason, payout)
