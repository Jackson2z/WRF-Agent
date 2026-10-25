"""CLI (public preview): python3 -m src.userguide <command> [options]

Docs -> parameter registry -> form:
    fetch     download the upstream snapshot named in sources.yaml (skips files already present unless --force)
    parse     snapshot -> build/userguide/registry_fields.yaml   [--build <label>: include a compiled build's variables]
    render    registry_fields + field_map + dictionary (+ --prefill) -> build/userguide/wrf_parameters.html
    all       fetch + parse + render

Cases:
    case-init <case> --request "..." [--draft draft.yaml]   create a case (request.md, draft.yaml, state.yaml)
    approve   <case> --confirm approve                       the human Submit: approval + compile checks
    batch     <case> [--batch f]                             expand the design matrix -> members/*

Measurements: python3 -m src.userguide.measure_audit / python3 -m src.userguide.measure_fidelity
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
    ap.add_argument("cmd", choices=["fetch", "parse", "render", "all", "case-init", "approve", "batch"])
    ap.add_argument("case", nargs="?", type=Path, help="case directory (case-init / approve / batch)")
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
    ap.add_argument("--confirm", help="approve: must be the literal word 'approve'")
    ap.add_argument("--strict", action="store_true", help="non-zero exit if field_map / dictionary / prefill has problems")
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
    else:
        if not a.case:
            ap.error(f"{a.cmd} needs a case directory")
        if a.cmd == "case-init":
            report["state"] = _dash.init_case(a.case, a.request, a.draft)
        elif a.cmd == "approve":
            out = _dash.approve(a.case, a.field_map, by="user", confirmation=a.confirm)
            report["approval"] = {"verdict": out["verdict"], "stage": out["state"]["current"],
                                  "failed_rules": [r["rule"] for r in out["failed"]]}
        elif a.cmd == "batch":
            from .batch import expand
            report["batch"] = expand(a.case, a.batch)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if a.strict and report.get("render", {}).get("problems"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
