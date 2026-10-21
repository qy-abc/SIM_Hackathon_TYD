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


def normalise_split(order_id: str, split: Mapping[str, float]) -> dict[str, int]:
    """Return *split* as whole percentages that sum to 100.

    Models sometimes send fractions ({"S04": 0.5, "S05": 0.5}) or floats
    ({"S04": 50.0, ...}) instead of percentages, so both are accepted.
    """
    total = sum(float(v) for v in split.values())
    scale = 100 if abs(total - 1) < 1e-6 else 1
    percents = {k: float(v) * scale for k, v in split.items()}
    if any(abs(v - round(v)) > 1e-6 or v < 0 for v in percents.values()):
        raise ValueError(
            f"split for '{order_id}' must be whole percentages, got {dict(split)}."
        )
    result = {k: int(round(v)) for k, v in percents.items()}
    if sum(result.values()) != 100:
        raise ValueError(
            f"split for '{order_id}' must sum to 100, got {dict(split)}."
        )
    return result


def record_ruling(
    order_id: str,
    ruled_by: str,
    ruling: str,
    split: Mapping[str, float] | None,
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
        split = normalise_split(order_id, split)
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
    split_overrides: Mapping[str, Mapping[str, float]] | None,
    open_disputes: Iterable[str] | None = None,
) -> list[Order]:
    """Return the orders with any ruled split swapped in."""
    overrides = split_overrides or {}
    both = sorted(set(overrides) & set(open_disputes or []))
    if both:
        raise ValueError(
            f"Orders {both} are in both open_disputes and split_overrides. A ruled "
            "order is no longer disputed: remove it from open_disputes and call again."
        )
    patched = []
    for order in orders:
        override = overrides.get(order.order_id)
        if override is not None:
            override = normalise_split(order.order_id, override)
            if not set(override) & set(order.split):
                # A ruling settles a dispute between people on the order; an
                # override naming none of them is almost always a mistyped ID.
                raise ValueError(
                    f"split_overrides for order '{order.order_id}' names "
                    f"{sorted(override)}, but that order's recorded salespeople "
                    f"are {sorted(order.split)}. Check the order ID against the "
                    "ruling from nyp_record_ruling."
                )
            order = order.model_copy(update={"split": override})
        patched.append(order)
    return patched


def inputs_used(
    approved_matches: Mapping[str, str] | None,
    open_disputes: Iterable[str] | None,
    split_overrides: Mapping[str, Mapping[str, float]] | None,
) -> dict[str, Any]:
    """Echo the state a result was computed with, so the agent can check it
    passed everything and carry the same values into its next call."""
    return {
        "approved_matches": dict(approved_matches or {}),
        "open_disputes": sorted(open_disputes or []),
        "split_overrides": {k: dict(v) for k, v in (split_overrides or {}).items()},
    }


def unconfirmed_warning(period: Any) -> dict[str, Any]:
    """List bank lines still unconfirmed after approved_matches was applied.

    A line the user confirmed earlier shows up here when the agent forgot
    to pass it back in, so the warning tells it to resend the full state.
    """
    lines = sorted(line.line_no for line in period.unmatched_lines)
    out: dict[str, Any] = {"unconfirmed_bank_lines": lines}
    if lines:
        out["warning"] = (
            f"Bank lines {lines} are not in approved_matches, so their orders "
            "are held. If the user already confirmed them, call again with the "
            "full approved_matches; otherwise ask the user to confirm them."
        )
    return out


def _owners() -> list[str]:
    """Return the staff_ids of every owner in the roster."""
    return [s.staff_id for s in load_staff() if s.role == "owner"]


def record_payout_decision(
    month: str, decided_by: str, decision: str, reason: str, payout: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the owner's approve/reject record for a payout draft. Nothing is stored.

    Only a staff member with role ``owner`` may decide. Any other caller
    raises a ``PermissionError`` that names who is authorised.
    """
    owners = _owners()
    if decided_by not in owners:
        names = ", ".join(f"{s.name} ({s.staff_id})" for s in load_staff() if s.staff_id in owners)
        raise PermissionError(
            f"'{decided_by}' is not authorised to approve or reject a payout. "
            f"Only the owner may: {names}."
        )
    decision = decision.strip().lower()
    if decision not in ("approve", "reject"):
        raise ValueError(f"decision must be 'approve' or 'reject', got '{decision}'.")
    approved = decision == "approve"
    return {
        "month": month,
        "decision": "approved" if approved else "rejected",
        "decided_by": decided_by,
        "decided_at": _now_iso(),
        "reason": reason,
        "total_dollars": payout["total_dollars"],
        "total_cents": payout["total_cents"],
        "payouts": payout["payouts"],
        "hold_count": payout["hold_count"],
        "status": (
            "approved by the owner: ready for payroll. Closer does not send money."
            if approved else
            "rejected by the owner: the payout stays a draft and nothing is paid."
        ),
        "log_entry": (
            f"{_now_iso()} {decided_by} {'approved' if approved else 'rejected'} the "
            f"{month} payout of S${payout['total_dollars']}"
            + (f": {reason}" if reason else "")
        ),
    }
