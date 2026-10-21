"""nyp_whoami — report who Orchestrate says is logged in.

A probe for tying staff IDs to the login instead of to what people type.
Orchestrate fills the system context variables wxo_email_id and
wxo_user_name for the user who invoked the agent; this tool only echoes
them, so we can see which ones Draft Preview and Live chat actually set.
"""
from __future__ import annotations

from ibm_watsonx_orchestrate.agent_builder.tools import tool
from ibm_watsonx_orchestrate.run import AgentRun


@tool(
    name="nyp_whoami",
    description=(
        "Report the logged-in user's email and user name as Orchestrate "
        "supplies them. Call it when the user asks 'who am I'."
    ),
)
def nyp_whoami(context: AgentRun) -> dict:
    """Return the system context variables that identify the caller.

    Returns
    -------
    dict
        wxo_email_id, wxo_user_name (None when Orchestrate did not set
        them) and context_keys, the names of every variable present.
    """
    ctx = context.request_context if context else {}
    return {
        "wxo_email_id": ctx.get("wxo_email_id"),
        "wxo_user_name": ctx.get("wxo_user_name"),
        "context_keys": sorted(ctx.keys()),
    }
