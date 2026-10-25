"""mcp_bench — the case tools of mcp_dashboard over streamable-http, plus namelist generation, for benchmark sidecars.

In the studio a person approves the specification before anything is generated, and there is deliberately no
submit tool. A benchmark has no such person: the agent is the modeller who asked for the namelists. This entry point
therefore adds one tool, generate_namelists, which generates from the current draft only when the compile check
passes. Everything else (request, draft, compile checks, WRF start-up checks) is the same code the studio uses.

Case directory: env WRF_AGENT_CASE (created if missing).
Run: python -m src.userguide.mcp_bench --host 0.0.0.0 --port 8000      # served at http://<host>:<port>/mcp
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import agent_tools as T
from . import checks, emit
from .mcp_dashboard import _case, mcp


@mcp.tool()
def generate_namelists() -> dict:
    """Generate namelist.wps and namelist.input from the current draft. Runs the compile checks first; nothing is
    generated while a check fails. Returns the verdict, the two files' text and any MISSING lines."""
    case = _case()
    draft = T._y(case / "draft.yaml") or {}
    design = draft.get("design") or {}
    rep = checks.run(design, T.design_ids())
    failed = [r for r in rep["rules"] if r["status"] == "FAIL"]
    if failed:
        return {"generated": False, "verdict": "FAIL", "failed": failed}
    spec = {"wrf_version": draft.get("wrf_version", "4.6.1"), "design": design}
    out = case / "generated"
    man = emit.emit(spec, out, force=True)
    return {"generated": True, "verdict": man["verdict"], "missing_required": man["missing_required"],
            "files": {name: (out / name).read_text(encoding="utf-8") for name in ("namelist.wps", "namelist.input")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    _case().mkdir(parents=True, exist_ok=True)
    mcp.run(transport="http", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
