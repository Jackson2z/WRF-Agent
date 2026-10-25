"""CLI: python3 -m src.userguide <command> [options]

Docs -> parameter registry -> form:
    fetch     download the upstream snapshot named in sources.yaml (skips files already present unless --force)
    parse     snapshot -> build/userguide/registry_fields.yaml   [--build <label>: include a compiled build's variables]
    render    registry_fields + field_map + dictionary (+ --prefill) -> build/userguide/wrf_parameters.html
    all       fetch + parse + render

Case lifecycle:
    case-init <case> --request "..." [--draft draft.yaml]   create a case (request.md, draft.yaml, state.yaml)
    discuss <case>                                           independent proposals -> cross-review -> synthesis
    select  <case> --alternative A                            record the human's chosen alternative (not approval)
    approve <case> --confirm approve                          the human Submit: approval + compile checks
    batch <case> [--batch f]                                 expand the design matrix -> members/* (pure backend)
    dashboard <case>                                         regenerate <case>/dashboard/*.html
    serve <case> [--port 8797]                               studio server (dashboard + agent chat)

Executable lifecycle (flow/stages.yaml — one durable state machine per case):
    flow-run <case> [--actor agent] [--max-steps N] [--dry-run]   action -> gate -> transition, with retries/back edges
    flow-status <case>                                           journal-derived state (current stage, attempts, claim)
    flow-graph                                                   print the stage graph as mermaid
    flow-board <case> [--post specialist.json]                    specialist handoff digests (five keys)

Measurements: python3 -m src.userguide.measure_audit / .measure_fidelity / .measure_fidelity
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import dashboard as _dash
from . import fetch as _fetch
from . import parse as _parse
from . import render as _render

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUILD = REPO / "build" / "userguide"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="userguide", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "parse", "render", "all", "case-init", "dashboard", "serve", "batch",
                                    "flow-run", "flow-status", "flow-graph", "flow-board", "discuss", "select",
                                    "approve"])
    ap.add_argument("case", nargs="?", type=Path, help="case directory (case-init / serve / flow commands)")
    ap.add_argument("--force", action="store_true", help="fetch: overwrite the existing snapshot")
    ap.add_argument("--snapshot", type=Path, help="snapshot directory (default references/upstream/wrf-<version>)")
    ap.add_argument("--registry", type=Path, default=BUILD / "registry_fields.yaml")
    ap.add_argument("--field-map", type=Path, default=HERE / "field_map.yaml")
    ap.add_argument("--dictionary", type=Path, default=HERE / "dictionary.yaml")
    ap.add_argument("--prefill", type=Path, help="render: agent draft YAML ({design: {<id>: {value, reason}}})")
    ap.add_argument("--out", type=Path, default=BUILD / "wrf_parameters.html")
    ap.add_argument("--request", help="case-init: the request in plain language")
    ap.add_argument("--draft", type=Path, help="case-init: draft YAML")
    ap.add_argument("--batch", type=Path, help="batch: design-matrix declaration (default <case>/batch.yaml)")
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--strict", action="store_true", help="non-zero exit if field_map / dictionary / prefill has problems")
    ap.add_argument("--actor", default="agent", help="flow-run: who acts (agent | automation | <specialist>)")
    ap.add_argument("--max-steps", type=int, default=32, help="flow-run: transition budget for one call")
    ap.add_argument("--dry-run", action="store_true", help="flow-run: evaluate the current stage without writing")
    ap.add_argument("--post", type=Path, help="flow-board: JSON file with a five-key digest to append")
    ap.add_argument("--alternative", help="select: id of human-chosen discussion alternative")
    ap.add_argument("--confirm", help="approve: must be the literal word 'approve' (the CLI form of Submit)")
    ap.add_argument("--no-open", action="store_true", help="serve: start the studio without opening a browser")
    ap.add_argument("--build", help="parse: build profile under references/upstream/wrf-<v>/builds/<label>")
    a = ap.parse_args(argv)

    report: dict = {}
    if a.cmd in ("fetch", "parse", "render", "all"):
        src = _fetch.load_sources()
        snap = a.snapshot or _fetch.snapshot_dir(src["version"])
        if a.cmd in ("fetch", "all"):
            report["snapshot"] = str(_fetch.fetch(force=a.force))
        if a.cmd in ("parse", "all"):
            bdir = (snap / "builds" / a.build) if a.build else None
            report["parse"] = _parse.write(snap, a.registry, bdir)
        if a.cmd in ("render", "all"):
            report["render"] = _render.render(a.registry, a.field_map, a.out, a.prefill, a.dictionary)
    elif a.cmd == "flow-graph":
        from .flow import dsl
        print(dsl.load().mermaid())
        return 0
    elif a.cmd in ("flow-run", "flow-status", "flow-board", "discuss", "select", "approve", "serve") and not a.case:
        ap.error(f"{a.cmd} needs a case directory")
    else:
        if not a.case:
            ap.error(f"{a.cmd} needs a case directory")
        if not a.registry.exists() and not (a.cmd.startswith("flow-") or a.cmd in ("discuss", "select", "approve")):
            ap.error("registry not built yet: run `python3 -m src.userguide all` first")
        if a.cmd == "flow-run":
            from .flow import runner
            report["flow"] = runner.run(a.case, actor=a.actor, max_steps=a.max_steps, dry_run=a.dry_run)
        elif a.cmd == "flow-status":
            from .flow import runner
            report["flow"] = runner.status(a.case)
        elif a.cmd == "discuss":
            from .flow.discussion import discuss
            result = discuss(a.case)
            report["discussion"] = {"status": result["status"], "error": result.get("error"),
                                    "alternatives": [x["id"] for x in result.get("synthesis", {}).get("alternatives", [])]}
        elif a.cmd == "select":
            from .flow.discussion import select
            if not a.alternative:
                ap.error("select requires --alternative")
            from .flow.runner import run as _flow_run
            picked = select(a.case, a.alternative)
            moved = _flow_run(a.case, actor="automation")   # Decompose passes only if its gate is satisfied
            report["selection"] = {"alternative": picked["selected_alternative"],
                                   "stage": moved.get("stage"), "status": moved.get("status")}
        elif a.cmd == "approve":
            out = _dash.approve(a.case, a.field_map, by="user", confirmation=a.confirm)
            report["approval"] = {"verdict": out["verdict"], "stage": out["state"]["current"],
                                  "failed_rules": [r["rule"] for r in out["failed"]]}
        elif a.cmd == "flow-board":
            from . import flow
            if a.post:
                digest = json.loads(a.post.read_text(encoding="utf-8"))
                report["posted"] = flow.board.post(a.case, actor=a.actor, digest=digest,
                                                  registry=flow.registry.Registry())
            report["board"] = {"rows": len(flow.board.rows(a.case)),
                               "open_questions": flow.board.open_questions(a.case),
                               "deviations": flow.board.deviations(a.case)}
        elif a.cmd == "case-init":
            report["state"] = _dash.init_case(a.case, a.request, a.draft)
            report["dashboard"] = str(_dash.build(a.case, a.field_map, a.dictionary, a.registry))
        elif a.cmd == "dashboard":
            report["dashboard"] = str(_dash.build(a.case, a.field_map, a.dictionary, a.registry))
        elif a.cmd == "batch":
            from .batch import expand
            report["batch"] = expand(a.case, a.batch)
        else:  # serve
            from .studio import main as studio_main
            argv = [str(a.case), "--port", str(a.port)] + (["--no-open"] if a.no_open else [])
            return studio_main(argv)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if a.strict and report.get("render", {}).get("problems"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
