"""Who is logged in, from Orchestrate's system context. No file writes.

Orchestrate passes the caller's login to any tool that takes an AgentRun
parameter (system context variables wxo_email_id and wxo_user_name). The
login -> staff ID map lives in config/logins.json, which is gitignored so
nobody's email is committed; scripts/deploy.py copies it into the package.

    {"owner.login@example.com": "S01"}

For judges who test with their own (or no) login, the file may also hold
"demo_fallback": true. Then an unlinked or missing login falls back to the
typed staff ID, as rulings do. A linked login is always held to its own ID.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOGINS_FILE = Path(__file__).resolve().parent.parent / "config" / "logins.json"


def _config() -> dict[str, Any]:
    if not LOGINS_FILE.is_file():
        return {}
    return json.loads(LOGINS_FILE.read_text("utf-8"))


def _logins() -> dict[str, str]:
    return {k.strip().lower(): v for k, v in _config().items() if isinstance(v, str)}


def demo_fallback() -> bool:
    """True when config/logins.json turns on the typed-ID fallback for judges."""
    return _config().get("demo_fallback") is True


def login_of(context: Any) -> str | None:
    """The caller's login email, or None when Orchestrate did not pass one."""
    ctx = getattr(context, "request_context", None) or {}
    email = ctx.get("wxo_email_id")
    return email.strip().lower() if email else None


def staff_id_for_login(context: Any) -> tuple[str | None, str | None]:
    """Return (login, staff_id); staff_id is None for an unlinked login."""
    login = login_of(context)
    return login, (_logins().get(login) if login else None)
