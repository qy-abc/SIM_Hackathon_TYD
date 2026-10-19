"""nyp_raise_dispute — record a commission dispute on an order.

Thin wrapper around tools._state.raise_dispute.  Persists to
data/state/disputes.json; that file is in .gitignore.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._state import raise_dispute


@tool(
    name="nyp_raise_dispute",
    description=(
        "Record a commission dispute on a specific order. The dispute is saved to "
        "data/state/disputes.json and will automatically hold that order out of "
        "any subsequent commission calculation until it is ruled on. "
        "Use nyp_record_ruling (sales manager only) to close the dispute."
    ),
)
def nyp_raise_dispute(
    order_id: str,
    raised_by: str,
    claim: str,
    state_file: Optional[str] = None,
) -> dict:
    """Raise (or update) a commission dispute for *order_id*.

    Parameters
    ----------
    order_id:
        The order being disputed, e.g. ``"1043"``.
    raised_by:
        Staff ID of the person raising the dispute, e.g. ``"S04"``.
    claim:
        Free-text description of the claim, e.g.
        ``"I closed this sale jointly with S05 — split should be 50/50."``.
    state_file:
        Override the default ``data/state/disputes.json`` (useful in tests).

    Returns
    -------
    dict
        order_id, raised_by, claim, raised_at, status.
    """
    sf = Path(state_file) if state_file else None
    return raise_dispute(order_id, raised_by, claim, state_file=sf)
