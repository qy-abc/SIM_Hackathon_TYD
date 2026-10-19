"""Minimal JSON-backed state store for disputes and rulings.

Layout of data/state/disputes.json:
{
  "disputes": {
    "<order_id>": {
      "raised_by": "<staff_id>",
      "claim":     "<free text>",
      "raised_at": "<ISO datetime>",
      "status":    "open" | "ruled"
    }
  },
  "rulings": {
    "<order_id>": {
      "ruled_by":  "<staff_id>",
      "ruling":    "<free text>",
      "split":     {"<staff_id>": <int>, ...} | null,
      "ruled_at":  "<ISO datetime>"
    }
  }
}

All writes are atomic: load → mutate → rewrite.  Concurrent writes from a
single-threaded agent are fine; we make no multi-process guarantees.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from closer.data.staff import load_staff

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_STATE_FILE = _DATA_ROOT / "state" / "disputes.json"


def _load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return {"disputes": {}, "rulings": {}}


def _save(state: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API used by the tool files
# ---------------------------------------------------------------------------

def raise_dispute(
    order_id: str,
    raised_by: str,
    claim: str,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Record a new dispute.  Idempotent: re-raising an already-open dispute
    updates the claim text but keeps the original raised_at timestamp."""
    path = state_file or _DEFAULT_STATE_FILE
    state = _load(path)
    existing = state["disputes"].get(order_id, {})
    state["disputes"][order_id] = {
        "raised_by": raised_by,
        "claim": claim,
        "raised_at": existing.get("raised_at", _now_iso()),
        "status": "open",
    }
    _save(state, path)
    return {"order_id": order_id, **state["disputes"][order_id]}


def _sales_managers() -> list[str]:
    """Return the staff_ids of every sales_manager in the roster."""
    return [s.staff_id for s in load_staff() if s.role == "sales_manager"]


def record_ruling(
    order_id: str,
    ruled_by: str,
    ruling: str,
    split: dict[str, int] | None,
    state_file: Path | None = None,
) -> dict[str, Any]:
    """Close a dispute and record the manager's ruling (and optional split override).

    Only a staff member with role ``sales_manager`` may rule.  Any other caller
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
    path = state_file or _DEFAULT_STATE_FILE
    state = _load(path)
    if order_id not in state["disputes"]:
        raise ValueError(f"No dispute on record for order '{order_id}'.")
    if split is not None and sum(split.values()) != 100:
        raise ValueError(f"split must sum to 100, got {dict(split)}.")
    state["disputes"][order_id]["status"] = "ruled"
    state["rulings"][order_id] = {
        "ruled_by": ruled_by,
        "ruling": ruling,
        "split": split,
        "ruled_at": _now_iso(),
    }
    _save(state, path)
    return {"order_id": order_id, **state["rulings"][order_id]}


def open_dispute_ids(state_file: Path | None = None) -> set[str]:
    """Return every order_id whose dispute is still open."""
    state = _load(state_file or _DEFAULT_STATE_FILE)
    return {oid for oid, d in state["disputes"].items() if d["status"] == "open"}


def split_override(order_id: str, state_file: Path | None = None) -> dict[str, int] | None:
    """Return the split from a ruling, or None if no ruling exists for this order."""
    state = _load(state_file or _DEFAULT_STATE_FILE)
    ruling = state["rulings"].get(order_id)
    return ruling["split"] if ruling else None


def list_disputes(state_file: Path | None = None) -> list[dict[str, Any]]:
    """Return all disputes (open and ruled) as a list."""
    state = _load(state_file or _DEFAULT_STATE_FILE)
    rows = []
    for oid, d in sorted(state["disputes"].items()):
        row = {"order_id": oid, **d}
        ruling = state["rulings"].get(oid)
        if ruling:
            row["ruling"] = ruling
        rows.append(row)
    return rows
