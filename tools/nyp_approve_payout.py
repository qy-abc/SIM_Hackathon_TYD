"""nyp_approve_payout — the owner approves or rejects the month's payout draft.

Rebuilds the draft from the same close state as nyp_prepare_payout, so the
owner decides on exactly those figures, then returns a who/what/when record.
Only the owner may decide, and the owner is taken from the Orchestrate
login (see tools/_identity.py), not from what is typed in the chat. Nothing
is stored and no money moves: the agent keeps the returned log_entry in the
conversation.
"""
from __future__ import annotations

from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool
from ibm_watsonx_orchestrate.run import AgentRun

from tools._identity import demo_fallback, staff_id_for_login
from tools._state import record_payout_decision
from tools._payout import build_payout


@tool(
    name="nyp_approve_payout",
    description=(
        "Record the owner's decision on the month's payout draft: 'approve' or "
        "'reject'. Only the owner may decide, checked against the Orchestrate "
        "login, not the typed staff ID; anyone else gets a PermissionError. "
        "In demo mode an unlinked login uses the typed staff ID instead. "
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
    context: AgentRun = None,
) -> dict:
    """Approve or reject the payout draft for *month*.

    Parameters
    ----------
    month:
        ISO year-month, e.g. ``"2026-09"``.
    decided_by:
        Staff ID the person gave for themselves, e.g. ``"S01"``. It must
        match the staff ID linked to their Orchestrate login, which must
        have role ``owner``.
    decision:
        ``"approve"`` or ``"reject"``.
    reason:
        Optional free text from the owner.
    approved_matches, open_disputes, split_overrides:
        The close state, exactly as passed to nyp_prepare_payout.
    data_root:
        Override the default ``data/`` directory (useful in tests).
    context:
        Filled by Orchestrate with the caller's login; never typed.

    Returns
    -------
    dict
        month, decision, decided_by, decided_at, reason, total_dollars,
        total_cents, payouts, hold_count, status, log_entry.
    """
    login, login_staff_id = staff_id_for_login(context)
    if login_staff_id is None and demo_fallback():
        payout = build_payout(month, approved_matches, open_disputes, split_overrides, data_root)
        record = record_payout_decision(month, decided_by, decision, reason, payout)
        record["login"] = login
        record["log_entry"] += f" (demo mode: typed ID, login {login or 'none'} not linked)"
        return record
    if login_staff_id is None:
        raise PermissionError(
            "Payout decisions need a login linked to a staff ID, and this "
            f"login ({login or 'none'}) is not linked to one."
        )
    if decided_by != login_staff_id:
        raise PermissionError(
            f"You typed '{decided_by}', but this login is linked to "
            f"'{login_staff_id}'. Decisions are recorded for the login only."
        )
    payout = build_payout(month, approved_matches, open_disputes, split_overrides, data_root)
    record = record_payout_decision(month, login_staff_id, decision, reason, payout)
    record["login"] = login
    record["log_entry"] += f" (login {login})"
    return record
