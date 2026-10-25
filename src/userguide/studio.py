"""studio — one command: build what is missing, start the studio server, open the browser.

    wrf-agent-studio [CASE] [--port 8797] [--no-open] [--model ID]

CASE defaults to $WRF_AGENT_CASES/default (WRF_AGENT_CASES defaults to ~/WRF-Agent-cases).
Model endpoint: WRF_AGENT_LLM_BASE_URL / WRF_AGENT_LLM_API_KEY / WRF_AGENT_LLM_MODEL. If no key is set
and a local OpenAI-compatible gateway is configured at ~/.pi/agent/models.json, the first provider that has a
key is used for this process only (read at start, never written anywhere).
"""

from __future__ import annotations

import argparse
import json
import os
import webbrowser
from pathlib import Path

from . import fetch, parse, server

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REGISTRY = REPO / "build" / "userguide" / "registry_fields.yaml"


def _llm_env() -> None:
    if os.environ.get("WRF_AGENT_LLM_API_KEY"):
        return
    cfg = Path.home() / ".pi" / "agent" / "models.json"
    try:
        providers = json.loads(cfg.read_text())["providers"]
    except (OSError, KeyError, ValueError):
        return
    prov = next((p for p in providers.values() if isinstance(p, dict) and p.get("apiKey")), None)
    if not prov:
        return
    os.environ["WRF_AGENT_LLM_API_KEY"] = prov.get("apiKey", "")
    os.environ.setdefault("WRF_AGENT_LLM_BASE_URL", prov.get("baseUrl", "http://127.0.0.1:3425/v1"))


def _ensure_registry() -> None:
    if REGISTRY.exists():
        return
    src = fetch.load_sources()
    snap = fetch.snapshot_dir(src["version"])
    if not (snap / "Registry" / "Registry.EM").exists() or not (snap / "users_guide").exists():
        fetch.fetch()
    parse.write(snap, REGISTRY)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wrf-agent-studio", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", nargs="?", type=Path)
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--model", help="override WRF_AGENT_LLM_MODEL")
    a = ap.parse_args(argv)

    case = a.case or Path(os.environ.get("WRF_AGENT_CASES", Path.home() / "WRF-Agent-cases")) / "default"
    if a.model:
        os.environ["WRF_AGENT_LLM_MODEL"] = a.model
    _llm_env()
    _ensure_registry()
    server.serve(case.resolve(), HERE / "field_map.yaml", HERE / "dictionary.yaml", REGISTRY, a.port,
                 on_ready=None if a.no_open else webbrowser.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
