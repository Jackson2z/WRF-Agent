"""study — the deliberation study harness: arms, one trial, one row.

Arms (identical request, model, budget; only the deliberation structure differs):
  multi     the full discussion: independent proposals -> cross-review -> synthesis (select() picks A)
  single    one specialist, no cross-review, no alternatives (discuss allow_single=True)
  oneshot   one direct request -> specification, no roles, no deliberation

A trial writes the same files the studio writes (request.md, discussion.yaml, draft.yaml), compiles the draft,
emits the namelists (temp dir) and runs WRF's start-up checks on them, then appends one row per arm to
study_record.yaml. Metrics come from deliberation_metrics; nothing is judged by a model.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import yaml

from . import actions, discussion
from .. import emit
from . import registry as flow_registry
from .. import checks, startup_checks as SC
from .deliberation_metrics import metrics


def _make_case(case: Path, request: str) -> Path:
    case.mkdir(parents=True, exist_ok=True)
    (case / "request.md").write_text(request.strip() + "\n", encoding="utf-8")
    (case / "state.yaml").write_text(yaml.safe_dump({"current": "request", "options": {}}), encoding="utf-8")
    return case


def arm_multi(case: Path, client, model) -> dict:
    rec = discussion.discuss(case, client=client, model=model)
    alts = rec.get("synthesis", {}).get("alternatives", [])
    if rec.get("status") != "complete" or not alts:
        raise discussion.DiscussionError("discussion incomplete: " + str(rec.get("error", "no alternatives"))[:120])
    first = sorted(a["id"] for a in alts)[0]
    discussion.select(case, first)          # A is chosen by id, not by reading the designs
    return {"discussion": rec, "alternative": first}


def arm_single(case: Path, client, model) -> dict:
    people = flow_registry.Registry().specialists
    rec = discussion.discuss(case, client=client, model=model, specialists=people[:1], allow_single=True)
    discussion.select(case, "A")
    return {"discussion": rec, "alternative": "A"}


def arm_oneshot(case: Path, client, model) -> dict:
    """No roles, no discussion: one request -> one specification (the self-refinement baseline's first step)."""
    reg = flow_registry.Registry()
    ids = reg.design_ids()
    request = (case / "request.md").read_text(encoding="utf-8").strip()
    raw: list = []
    answer = discussion._answer(client, model,
                                "You configure WRF runs. Answer with ONE JSON object and nothing else, shaped "
                                'exactly like {"design": {"<field_id>": {"value": "<string>", "reason": '
                                '"<one sentence>"}}, "open_questions": ["..."]}. Every id in design_ids must '
                                "appear exactly once with a non-empty value and a reason; per-domain fields take a "
                                "comma-separated string such as '1,0,0'.",
                                {"request": request, "design_ids": ids},
                                check=lambda r: discussion._design(r.get("design"), ids),
                                raw=raw)
    snapshot = actions.fingerprint(request)
    record = {"status": "complete", "snapshot_hash": snapshot, "request": request, "model": model,
              "proposals": {"oneshot": answer}, "critiques": {}, "raws": {"oneshot": raw},
              "synthesis": {"alternatives": [{"id": "A", "design": answer["design"],
                                              "tradeoffs": ["one-shot arm: no deliberation"]}],
                            "disagreements": [], "open_questions": answer.get("open_questions", [])}}
    (case / "discussion.yaml").write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=False), encoding="utf-8")
    draft = {"wrf_version": "4.6.1", "discussion_hash": snapshot, "selected_alternative": "A",
             "design": answer["design"]}
    (case / "draft.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"discussion": record, "alternative": "A"}


ARMS = {"multi": arm_multi, "single": arm_single, "oneshot": arm_oneshot}


def run_trial(request: str, arms=("multi", "single", "oneshot"), client=None, model=None,
              root: Path | None = None, trial_id: str = "t00") -> dict:
    """One request through every arm; returns the row appended to study_record.yaml."""
    root = root or Path(tempfile.mkdtemp(prefix="delib_"))
    if client is None:
        from .. import chat
        from openai import OpenAI
        client = OpenAI(base_url=chat.settings()["base_url"],
                        api_key=os.environ.get("WRF_AGENT_LLM_API_KEY") or "none")
    if model is None:
        from .. import chat
        model = chat.settings()["model"]
    row: dict = {"trial": trial_id, "request": request[:400], "model": model, "arms": {}}
    for arm in arms:
        case = root / trial_id / arm
        _make_case(case, request)
        t0 = time.perf_counter()
        try:
            ARMS[arm](case, client, model)
            design = yaml.safe_load((case / "draft.yaml").read_text(encoding="utf-8"))["design"]
            plain = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in design.items()}
            rep = checks.run(plain, list(plain))
            failed = [r for r in rep["rules"] if r["status"] == "FAIL"]
            emit_ok, startup = None, None
            if not failed:
                with tempfile.TemporaryDirectory() as tmp:
                    man = emit.emit({"wrf_version": "4.6.1", "design": plain}, Path(tmp), force=True)
                    emit_ok = man["verdict"]
                    sc = SC.evaluate(SC.parse_namelist((Path(tmp) / "namelist.input").read_text()))
                    startup = {"refusals": len(sc["refusals"]),
                               "rewrites_of_written": len(sc["rewrites_of_written_values"])}
            row["arms"][arm] = {
                "ok": True, "wall_s": round(time.perf_counter() - t0, 1),
                "compile_verdict": rep["verdict"], "failed_rules": [r["rule"] for r in failed],
                "emit_verdict": emit_ok, "startup": startup,
                "metrics": metrics(case)}
        except Exception as exc:  # noqa: BLE001 - a failed arm is a result, reported as such
            row["arms"][arm] = {"ok": False, "wall_s": round(time.perf_counter() - t0, 1),
                                "error": f"{type(exc).__name__}: {exc}"}
    return row


def run_study(requests: list[str], out: Path, client=None, model=None, arms=("multi", "single", "oneshot")) -> Path:
    """Append one row per request to a study record (JSONL, one trial per line, resumable by trial id)."""
    done = set()
    if out.exists():
        done = {json.loads(l)["trial"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    root = out.parent / "cases"
    with out.open("a", encoding="utf-8") as fh:
        for i, req in enumerate(requests):
            tid = f"t{i:02d}"
            if tid in done:
                continue
            row = run_trial(req, arms=arms, client=client, model=model, root=root, trial_id=tid)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
    return out
