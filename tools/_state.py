"""Dispute and ruling helpers shared by the nyp_ tools. No file I/O.

watsonx Orchestrate runs every Python tool on its own read-only filesystem,
so tools cannot save state between calls. Dispute state therefore travels
through the conversation instead: the agent passes it back in on every call.

  open_disputes    list of order_ids with an open dispute, e.g. ["1043"].
                   Those orders are held out of commission and payout.
  split_overrides  order_id -> split from a manager's ruling,
                   e.g. {"1043": {"S04": 50, "S05": 50}}.

nyp_raise_dispute and nyp_record_ruling return records that tell the agent
how to update those two values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from closer.data.staff import load_staff
from closer.data.models import Order


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _staff_ids() -> set[str]:
    return {s.staff_id for s in load_staff()}


def raise_dispute(order_id: str, raised_by: str, claim: str) -> dict[str, Any]:
    """Return a new open-dispute record. Nothing is stored."""
    if raised_by not in _staff_ids():
        raise ValueError(f"Unknown staff_id '{raised_by}'.")
    return {
        "order_id": order_id,
        "raised_by": raised_by,
        "claim": claim,
        "raised_at": _now_iso(),
        "status": "open",
        "next_step": (
            f"Add '{order_id}' to open_disputes on every later commission, "
            "statement or payout call. Only a sales manager can rule on it."
        ),
    }


def _sales_managers() -> list[str]:
    """Return the staff_ids of every sales_manager in the roster."""
    return [s.staff_id for s in load_staff() if s.role == "sales_manager"]


def record_ruling(
    order_id: str,
    ruled_by: str,
    ruling: str,
    split: Mapping[str, int] | None,
    open_disputes: Iterable[str] | None,
) -> dict[str, Any]:
    """Return the manager's ruling on an open dispute. Nothing is stored.

    Only a staff member with role ``sales_manager`` may rule. Any other caller
    raises a ``PermissionError`` that names who is authorised.
    """
    managers = _sales_managers()
    if ruled_by not in managers:
        names = ", ".join(
            f"{s.name} ({s.staff_id})"
            for s in load_staff()
            if s.staff_id in managers
        )
        raise PermissionError(
            f"'{ruled_by}' is not authorised to record a ruling. "
            f"Only sales managers may rule: {names}."
        )
    if order_id not in set(open_disputes or []):
        raise ValueError(f"No open dispute for order '{order_id}'.")
    if split is not None:
        unknown = set(split) - _staff_ids()
        if unknown:
            raise ValueError(f"Unknown staff_id in split: {sorted(unknown)}.")
        if sum(split.values()) != 100:
            raise ValueError(f"split must sum to 100, got {dict(split)}.")
    next_step = f"Remove '{order_id}' from open_disputes."
    if split is not None:
        next_step += f" Pass split_overrides {{'{order_id}': {dict(split)}}} on later calls."
    return {
        "order_id": order_id,
        "ruled_by": ruled_by,
        "ruling": ruling,
        "split": dict(split) if split is not None else None,
        "ruled_at": _now_iso(),
        "status": "ruled",
        "next_step": next_step,
    }


def apply_split_overrides(
    orders: Iterable[Order],
    split_overrides: Mapping[str, Mapping[str, int]] | None,
) -> list[Order]:
    """Return the orders with any ruled split swapped in."""
    overrides = split_overrides or {}
    patched = []
    for order in orders:
        override = overrides.get(order.order_id)
        if override is not None:
            if sum(override.values()) != 100:
                raise ValueError(
                    f"split_overrides for '{order.order_id}' must sum to 100, "
                    f"got {dict(override)}."
                )
            order = order.model_copy(update={"split": dict(override)})
        patched.append(order)
    return patched
