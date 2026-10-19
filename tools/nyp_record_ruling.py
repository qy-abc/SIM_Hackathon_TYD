"""nyp_record_ruling — close a dispute and record the manager's ruling.

Thin wrapper around tools._state.record_ruling.  Only a staff member whose
role is sales_manager may call this; the check is enforced inside _state and
raises PermissionError for any other caller.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._state import record_ruling


@tool(
    name="nyp_record_ruling",
    description=(
        "Close an open dispute and record the sales manager's ruling. "
        "Only a staff member with the sales_manager role (e.g. Jason, S02) may "
        "call this tool — any other caller receives a PermissionError. "
        "An optional split override re-assigns commission for that order from "
        "the next nyp_compute_commission run onwards."
    ),
)
def nyp_record_ruling(
    order_id: str,
    ruled_by: str,
    ruling: str,
    split: Optional[dict[str, int]] = None,
    state_file: Optional[str] = None,
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
        Must sum to 100. Pass ``null`` / omit to leave the original split.
    state_file:
        Override the default ``data/state/disputes.json`` (useful in tests).

    Returns
    -------
    dict
        order_id, ruled_by, ruling, split, ruled_at.
    """
    sf = Path(state_file) if state_file else None
    return record_ruling(order_id, ruled_by, ruling, split, state_file=sf)
