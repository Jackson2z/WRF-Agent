#!/usr/bin/env python3
"""wrfa_demo — run one request through the pipeline: case -> discussion -> selection -> approval -> compile -> emit.

Two modes:
  default      uses the model (WRF_AGENT_LLM_BASE_URL / WRF_AGENT_LLM_API_KEY / WRF_AGENT_LLM_MODEL):
               specialists discuss the request, alternative A is selected, the human approval step is
               represented by --confirm approve (the demo is single-user)
  --no-llm     deterministic: seeds the case from cases/singapore_albedo/draft.yaml instead of a discussion

Nothing is submitted anywhere; the run lifecycle beyond emission (WPS/WRF on your cluster) stays with you.

    python wrfa_demo.py --output ./demo_out                 # with a model
    python wrfa_demo.py --output ./demo_out --no-llm        # fully deterministic, no model, no network
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from src.userguide import checks, dashboard, emit  # noqa: E402
from src.userguide import startup_checks as SC  # noqa: E402


def build_case(out: Path, request: str, draft: Path | None) -> Path:
    case = out / "case"
    case.mkdir(parents=True, exist_ok=True)
    (case / "request.md").write_text(request.strip() + "\n", encoding="utf-8")
    if draft:
        shutil.copyfile(draft, case / "draft.yaml")
        dashboard.init_case(case, request, draft)
    return case


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--prompt", type=Path, default=REPO / "user_requirement.txt")
    ap.add_argument("--no-llm", action="store_true", help="deterministic mode: seed from the example draft")
    ap.add_argument("--confirm", default="approve", help="must be 'approve' for the pipeline to continue")
    a = ap.parse_args(argv)

    request = a.prompt.read_text(encoding="utf-8")
    case = build_case(a.output, request, None if not a.no_llm else REPO / "cases/singapore_albedo/draft.yaml")

    if not a.no_llm:
        from src.userguide.flow import discussion
        rec = discussion.discuss(case)
        if rec.get("status") != "complete":
            print(json.dumps({"error": "discussion incomplete", "detail": rec.get("error")}, indent=1))
            return 1
        discussion.select(case, "A")            # the demo picks A by id; a person would read them

    fm = REPO / "src/userguide/field_map.yaml"
    out = dashboard.approve(case, fm, by="user", confirmation=a.confirm)
    if out["verdict"] != "PASS":
        print(json.dumps({"approval": "refused", "failed": [r["rule"] for r in out["failed"]]}, indent=1))
        return 1

    spec = __import__("yaml").safe_load((case / "experiment.yaml").read_text(encoding="utf-8"))
    man = emit.emit(spec, a.output / "build")
    report = {"case": str(case), "compile": "PASS", "emit": {k: man[k] for k in ("verdict", "files", "startup_checks")},
              "build_dir": str(a.output / "build")}
    print(json.dumps(report, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
