"""mcp_dashboard — the same case tools as the in-page chat agent, exposed over MCP (stdio) for terminal agents.

Case directory: env WRF_AGENT_CASE.  Run: python -m src.userguide.mcp_dashboard
"""

from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP

from . import agent_tools as T

mcp = FastMCP("wrf-dashboard")


def _case() -> Path:
    c = os.environ.get("WRF_AGENT_CASE")
    if not c:
        raise RuntimeError("WRF_AGENT_CASE is not set")
    return Path(c)


@mcp.tool()
def case_state() -> dict:
    """Current workflow stage, per-stage status, the user's request and the last compile verdict."""
    return T.case_state(_case())


@mcp.tool()
def design_fields() -> list[dict]:
    """All fields the user must decide, with options, rules and the current draft value/reason."""
    return T.design_fields(_case())


@mcp.tool()
def lookup_parameter(name: str) -> list[dict]:
    """Look up any WRF/WPS namelist parameter in the Users' Guide / Registry."""
    return T.lookup_parameter(_case(), name)


@mcp.tool()
def set_request(text: str) -> dict:
    """Record the user's request in plain language."""
    return T.set_request(_case(), text)


@mcp.tool()
def set_draft(values: dict, note: str = "") -> dict:
    """Propose design values: {field id: {value, reason}}. The user approves with Submit."""
    return T.set_draft(_case(), values, note)


@mcp.tool()
def check_draft() -> dict:
    """Run the compile checks on the current draft without submitting."""
    return T.check_draft(_case())


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
