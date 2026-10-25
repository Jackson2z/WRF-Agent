"""flow.actions — the work an automatic stage performs.

Actions write files in the case directory and return a small dict; the stage's gate then decides whether the
work was good enough to leave the stage. No action submits a job: launching is prepare-only by design.

Every function here is either an action (does work) or a gate (``gate_*``, reads artefacts and returns a
mode). Keeping them in one module lets the stage spec name them as ``userguide.flow.actions:<name>``.
"""

from __future__ import annotations

import hashlib
import json
import datetime as dt
from pathlib import Path

import yaml

from .. import checks, emit, runops


def _y(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def _w(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _value(v):
    return v.get("value") if isinstance(v, dict) else v


def design_ids() -> list[str]:
    from . import registry
    return registry.Registry().design_ids()


def _as_list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def required_design(design: dict) -> list[str]:
    """Design fields required *now*: a show_if field counts only when its condition holds
    (truelat1/2 exist for lambert/polar, use_wudapt_lcz only for an urban scheme, ...)."""
    from . import registry
    r = registry.Registry()
    out = []
    for fid in r.design_ids():
        cond = (r._fields.get(fid) or {}).get("show_if") or {}
        ok = True
        for ref, allowed in cond.items():
            cur = _as_list(_value(design.get(ref)))
            if not any(str(a) in cur for a in allowed):
                ok = False
                break
        if ok:
            out.append(fid)
    return out


# --------------------------------------------------------------------------- gates on the human surface


def gate_draft_ready(case: Path) -> dict:
    """Decompose is complete when the experts have deliberated, a person has selected an alternative, and every
    required design field carries a value and a reason.

    The deliberation belongs to this stage: a missing discussion is a *wait* (the work has not been started), an
    incomplete discussion or an unselected one is a *fail* with an instruction, and only a selected draft can
    leave the stage for Review.
    """
    draft = _y(case / "draft.yaml") or {}
    record = _y(case / "discussion.yaml")
    if record is None:
        if not draft:
            return {"ok": False, "wait": True,
                    "detail": "no design discussion yet — run `discuss` (independent expert proposals)"}
    else:
        if record.get("status") != "complete":
            return {"ok": False, "detail": f"discussion {record.get('status')}: {str(record.get('error'))[:160]}"}
        if draft.get("discussion_hash") != record.get("snapshot_hash") or not draft.get("selected_alternative"):
            return {"ok": False, "wait": True,
                    "detail": "discussion finished — select an alternative to leave Decompose"}
    d = draft.get("design") or {}
    required = required_design(d)
    missing = [k for k in required if _value(d.get(k)) in (None, "")]
    no_reason = [k for k in required if k in d and isinstance(d[k], dict)
                 and not str(d[k].get("reason") or "").strip()]
    if missing:
        return {"ok": False, "detail": f"{len(missing)} required design field(s) without a value: {missing[:6]}"}
    if no_reason:
        return {"ok": False, "detail": f"{len(no_reason)} proposal(s) without a reason: {no_reason[:6]}"}
    return {"ok": True, "detail": f"{len(required)} required design fields proposed with reasons "
                                  f"({len(design_ids())} exist; the rest are not applicable yet)"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def approval_inputs(case):
    return {"spec": fingerprint(_y(case / "experiment.yaml")),
            "draft": fingerprint(_y(case / "draft.yaml")),
            "request": fingerprint((case / "request.md").read_text() if (case / "request.md").exists() else ""),
            "discussion": fingerprint(_y(case / "discussion.yaml"))}


def invalidate_approval(case, reason):
    _w(case / "approval.yaml", {"valid": False, "reason": reason})


def gate_spec_approved(case: Path) -> dict:
    approval = _y(case / "approval.yaml") or {}
    if not approval.get("valid") or approval.get("inputs") != approval_inputs(case):
        return {"ok": False, "wait": True, "detail": "plan changed or approval absent/invalidated; human Submit required"}
    return {"ok": True, "detail": "human approval matches request, draft, discussion and specification"}


# --------------------------------------------------------------------------- compile


def compile_case(case: Path) -> dict:
    spec = _y(case / "experiment.yaml") or {}
    design = spec.get("design") or {}
    report = checks.run(design, design_ids())
    report["checked_at"] = _now()
    _w(case / "compile_report.yaml", report)
    failed = [r["rule"] for r in report.get("rules", []) if r["status"] == "FAIL"]
    return {"ok": report.get("verdict") == "PASS", "verdict": report.get("verdict"), "failed": failed,
            "detail": f"compile {report.get('verdict')}" + (f"; rules FAIL: {failed}" if failed else "")}


def gate_compile_pass(case: Path) -> dict:
    rep = _y(case / "compile_report.yaml")
    if not rep:
        return {"ok": False, "detail": "no compile_report.yaml (compile action did not run)"}
    failed = [f"{r['rule']}: {r['detail']}" for r in rep.get("rules", []) if r["status"] == "FAIL"]
    return {"ok": rep.get("verdict") == "PASS",
            "detail": f"verdict {rep.get('verdict')}" + (f"; {failed[:3]}" if failed else "")}


# --------------------------------------------------------------------------- launch (prepare only)


def prepare_launch(case: Path) -> dict:
    if (case / "restart_record.yaml").exists():
        return {"ok": False, "wait": True, "detail": "restart requires human authorization; inspect build/restart, initial spec will not be emitted"}
    approved = gate_spec_approved(case)
    if not approved.get("ok"):
        return approved
    spec = _y(case / "experiment.yaml")
    if not spec:
        return {"ok": False, "detail": "no approved experiment.yaml"}
    rep = emit.emit(spec, case / "build", force=True)
    _w(case / "build" / "emit_report.yaml", {"emitted_at": _now(), **rep})
    if rep.get("verdict") != "COMPLETE":
        return {"ok": False, "detail": f"emit {rep.get('verdict')}: gaps {rep.get('missing')}",
                "emit": rep.get("verdict")}
    prep = runops.prepare_launch(case)
    return {"ok": bool(prep.get("ok")), "detail": prep.get("error") or f"prepared {prep.get('run_dir')}",
            "emit": rep.get("verdict"), "run_dir": prep.get("run_dir")}


def gate_launch_prepared(case: Path) -> dict:
    man = _y(case / "build" / "emit_manifest.yaml") or {}
    rm = _y(case / "run" / "run_manifest.yaml") or {}
    if man.get("verdict") != "COMPLETE":
        return {"ok": False, "detail": f"emit manifest verdict {man.get('verdict')!r}"}
    if not rm:
        return {"ok": False, "detail": "no run/run_manifest.yaml"}
    if rm.get("submitted"):
        return {"ok": True, "detail": "run directory prepared; submission is a user action"}
    return {"ok": True, "detail": "run directory prepared (not submitted — user action)"}


# --------------------------------------------------------------------------- monitor / verify / restart


def gate_run_finished(case: Path) -> dict:
    run = case / "run"
    if not run.is_dir():
        return {"ok": False, "wait": True, "detail": "no run directory yet (submission is a user action)"}
    s = runops.survey(run)
    state = s.get("state")
    if state == "finished":
        return {"ok": True, "detail": "rsl reports SUCCESS COMPLETE WRF"}
    if state == "stalled":
        return {"ok": False, "detail": f"stalled: newest file {s.get('newest_file_age_min')} min old"}
    if state == "empty":
        return {"ok": False, "wait": True, "detail": "run directory has no frames yet"}
    return {"ok": False, "wait": True, "detail": f"run state {state!r} (frames written)"}


def verify_case(case: Path) -> dict:
    spec = _y(case / "experiment.yaml") or {}
    design = spec.get("design") or {}
    def first(key):
        v = _value(design.get(key))
        return v[0] if isinstance(v, list) else str(v).split(",")[0].strip().strip("'\"")
    try:
        start, end = first("wps:start_date"), first("wps:end_date")
        cadence = float(first("input:history_interval"))
        span = (dt.datetime.fromisoformat(end.replace("_", "T")) -
                dt.datetime.fromisoformat(start.replace("_", "T"))).total_seconds() / 60
        if cadence <= 0 or span <= 0 or span % cadence:
            raise ValueError("window must be positive and divisible by cadence")
        count = int(span / cadence) + 1
    except (ValueError, TypeError):
        return {"ok": False, "wait": True, "detail": "approved window and output cadence required for verification"}
    rep = runops.verify(case / "run", expect_frames=count, interval_min=cadence,
                        start=start, end=end)
    rep["checked_at"] = _now()
    _w(case / "verify_report.yaml", rep)
    return {"ok": rep.get("verdict") == "PASS", "detail": f"verify {rep.get('verdict')}"}


def gate_verify_pass(case: Path) -> dict:
    rep = _y(case / "verify_report.yaml")
    if not rep:
        return {"ok": False, "detail": "no verify_report.yaml"}
    bad = [c["check"] for c in rep.get("checks", []) if not c.get("ok")]
    return {"ok": rep.get("verdict") == "PASS",
            "detail": f"verdict {rep.get('verdict')}" + (f"; {bad}" if bad else "")}


def restart_case(case: Path) -> dict:
    out = runops.restart_override(case)
    _w(case / "restart_record.yaml", {"at": _now(), **out})
    return {"ok": bool(out.get("ok")),
            "detail": out.get("error") or f"restart spec from {out.get('restart', {}).get('from')}"}


def gate_restart_done(case: Path) -> dict:
    rec = _y(case / "restart_record.yaml")
    if not rec:
        return {"ok": False, "detail": "no restart_record.yaml"}
    return {"ok": False, "wait": bool(rec.get("ok")), "detail": rec.get("error") or "restart artifact prepared; human authorization required"}
