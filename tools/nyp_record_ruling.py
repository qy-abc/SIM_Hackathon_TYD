"""nyp_record_ruling — close a dispute and record the manager's ruling.

Thin wrapper around tools._state.record_ruling.  Only a staff member whose
role is sales_manager may call this; the check is enforced inside _state and
raises PermissionError for any other caller. Nothing is saved; the agent
passes the returned split in split_overrides on later calls.
"""
from __future__ import annotations

from typing import Optional

from ibm_watsonx_orchestrate.agent_builder.tools import tool

from tools._state import record_ruling


@tool(
    name="nyp_record_ruling",
    description=(
        "Close an open dispute and record the sales manager's ruling. "
        "Only a staff member with the sales_manager role (e.g. Jason, S02) may "
        "call this tool — any other caller receives a PermissionError. "
        "The order must be in open_disputes. Nothing is saved: afterwards, remove "
        "the order from open_disputes and, if a split was given, pass it in "
        "split_overrides to later commission, statement and payout calls."
    ),
)
def nyp_record_ruling(
    order_id: str,
    ruled_by: str,
    ruling: str,
    split: Optional[dict[str, float]] = None,
    open_disputes: Optional[list[str]] = None,
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
        order_id, ruled_by, ruling, split, ruled_at, status, next_step.
    """
    return record_ruling(order_id, ruled_by, ruling, split, open_disputes)
