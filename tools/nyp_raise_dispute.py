"""nyp_raise_dispute — open a commission dispute on an order.

Thin wrapper around tools._state.raise_dispute. Nothing is saved: the
Orchestrate tool runtime is read-only, so the agent carries the order_id in
open_disputes on later calls.
"""
from __future__ import annotations

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._state import raise_dispute


@tool(
    name="nyp_raise_dispute",
    description=(
        "Open a commission dispute on a specific order. Nothing is saved and no "
        "pay changes: the order is held only when you pass its order_id in "
        "open_disputes to nyp_compute_commission, nyp_explain_statement and "
        "nyp_prepare_payout. Only a sales manager can close it with "
        "nyp_record_ruling."
    ),
)
def nyp_raise_dispute(order_id: str, raised_by: str, claim: str) -> dict:
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

    Returns
    -------
    dict
        order_id, raised_by, claim, raised_at, status, next_step.
    """
    return raise_dispute(order_id, raised_by, claim)
