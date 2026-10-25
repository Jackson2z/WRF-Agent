"""agent_tools — the tools an agent uses to work on a case, shared by the in-page chat agent and MCP.

The agent and the user share one source of truth: the case directory (request.md, draft.yaml, state.yaml).
The agent records the request and proposes values with reasons; the user approves with Submit.
There is deliberately no submit tool: approval stays with the person.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import checks, dashboard, render

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REGISTRY = REPO / "build" / "userguide" / "registry_fields.yaml"


def _y(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def _w(p: Path, data, header: str = "") -> None:
    p.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def _merged(case: Path) -> dict:
    return render.merge(_y(REGISTRY), _y(HERE / "field_map.yaml"), _y(HERE / "dictionary.yaml"), _y(case / "draft.yaml"))


def design_ids() -> list[str]:
    fm = _y(HERE / "field_map.yaml")
    return [k for k, v in fm["fields"].items() if v.get("class") == "design"]


# --------------------------------------------------------------------------- tools


def case_state(case: Path) -> dict:
    st = dashboard.load_state(case)
    rep = _y(case / "compile_report.yaml") or {}
    req = case / "request.md"
    return {
        "current_stage": st["current"], "status": st["status"],
        "request": req.read_text(encoding="utf-8") if req.exists() else None,
        "last_compile": {"verdict": rep.get("verdict"),
                         "failed": [r for r in rep.get("rules", []) if r["status"] == "FAIL"]} if rep else None,
        "recent_history": st["history"][-5:],
    }


def design_fields(case: Path) -> list[dict]:
    out = []
    for f in _merged(case)["fields"]:
        if f["class"] == "design":
            out.append({"id": f["id"], "label": f["label"], "question": f["question"], "unit": f["unit"],
                        "per_domain": bool(f["per_domain_hint"]),
                        "constraint": next((c for k, c in checks.constraints().items() if k == f["id"]), None),
                        "options": [{"value": o["value"], "label": o["label"]} for o in f["options"]],
                        "rules": f["rules"], "show_if": f["show_if"], "draft": f["prefill"]})
    return out


def lookup_parameter(case: Path, name: str) -> list[dict]:
    name = name.strip().lower().split(":")[-1]
    hits = [f for f in _merged(case)["fields"] if f["name"] == name] or \
           [f for f in _merged(case)["fields"] if name in f["name"]]
    return [{"id": f["id"], "class": f["class"], "block": f["block"], "default": f["default"],
             "per_domain": f["per_domain"], "description": f["desc_official"],
             "options": [{"value": o["value"], "label": o["official"] or o["label"]} for o in f["options"]],
             "derived_from": f["derive_from"], "source": f["ug_url"]} for f in hits[:10]]


@dashboard.case_write
def set_request(case: Path, text: str) -> dict:
    case.mkdir(parents=True, exist_ok=True)
    (case / "request.md").write_text(text.strip() + "\n", encoding="utf-8")
    from .flow.actions import invalidate_approval
    invalidate_approval(case, "request edited")
    dashboard.move(case, "decompose", "request edited")
    st = dashboard.load_state(case)
    if st["current"] == "request":
        dashboard.move(case, "decompose", "request recorded")
        st = dashboard.load_state(case)
    _w(case / "state.yaml", st)
    return {"ok": True}


def _domain_text(v):
    """A per-domain value as the comma-separated text the draft stores; models often send a list [1, 0, 0]."""
    return ",".join(str(x).strip() for x in v) if isinstance(v, (list, tuple)) else v


@dashboard.case_write
def set_draft(case: Path, values: dict, note: str = "") -> dict:
    ids = set(design_ids())
    bad = [k for k in values if k not in ids]
    if bad:
        return {"accepted": False, "error": f"not design fields: {bad}", "design_ids": sorted(ids)}
    # declared domain constraints (field_map.yaml): refuse instead of letting wrf.exe rewrite or reject the value
    for fid, con in checks.constraints().items():
        if fid not in values:
            continue
        v = values[fid]
        v = v.get("value") if isinstance(v, dict) else v
        parts = checks._list(v)          # one entry per domain, whether the value came as a list or as "1,0,0"
        if con["kind"] == "same_all_domains" and len(set(parts)) > 1:
            return {"accepted": False, "field": fid,
                    "error": f"{fid.split(':')[1]} must be one value for every domain — {con['because']} "
                             f"({con['source']}). Write a single value, not {v}.",
                    "suggestion": str(parts[-1])}
        if con["kind"] == "equal_or_zero" and parts and any(x not in (parts[0], 0) for x in parts[1:]):
            return {"accepted": False, "field": fid,
                    "error": f"{fid.split(':')[1]}: inner domains repeat d01 or use 0 — {con['because']} "
                             f"({con['source']}); got {v}"}
    draft = _y(case / "draft.yaml") or {"wrf_version": "4.6.1", "design": {}}
    draft.setdefault("design", {})
    for k, v in values.items():
        v = v if isinstance(v, dict) else {"value": v}
        draft["design"][k] = {"value": _domain_text(v.get("value")), "reason": v.get("reason") or ""}
    _w(case / "draft.yaml", draft,
       "# Agent draft (stage 2: Decompose). Values and reasons proposed by the agent; the user approves in Review.\n")
    from .flow.actions import gate_draft_ready, invalidate_approval
    invalidate_approval(case, "draft edited")
    ready = gate_draft_ready(case)
    if ready["ok"] and dashboard.load_state(case)["current"] == "decompose":
        dashboard.move(case, "review", f"draft complete: {ready['detail']}")
    ev = f"agent updated {len(values)} draft value(s)" + (f": {note}" if note else "")
    st = dashboard.load_state(case)
    st["history"].append({"time": dashboard.now(), "event": ev})
    _w(case / "state.yaml", st)
    rep = checks.run(draft["design"], sorted(ids))
    return {"accepted": True, "updated": sorted(values), "check": {
        "verdict": rep["verdict"],
        "failed": [r for r in rep["rules"] if r["status"] == "FAIL"],
        "warnings": [r for r in rep["rules"] if r["status"] == "WARN"]}}


def post_digest(case: Path, digest: dict, specialist: str | None = None, task: str | None = None) -> dict:
    """Record a specialist handoff digest (five keys) on the case board."""
    from . import flow
    reg = flow.registry.Registry()
    actor = specialist or "agent"
    try:
        row = flow.board.post(case, actor=actor, digest=digest, registry=reg, task=task)
    except flow.board.BoardError as exc:
        return {"accepted": False, "error": str(exc)}
    return {"accepted": True, "at": row["at"], "actor": actor, "rows": len(flow.board.rows(case))}


def check_draft(case: Path) -> dict:
    draft = _y(case / "draft.yaml") or {}
    rep = checks.run(draft.get("design") or {}, design_ids())
    return {"verdict": rep["verdict"], "rules": rep["rules"], "derived": rep["derived"]}


# --------------------------------------------------------------------------- function-calling specs

SPECS = [
    {"name": "case_state", "description": "Current workflow stage, per-stage status, the user's request and the last compile verdict.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "design_fields", "description": "All fields the user must decide (class=design): id, label, question, options, compile rules, per-domain flag, current draft value and reason. Use these ids in set_draft.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "lookup_parameter", "description": "Look up any WRF/WPS namelist parameter in the Users' Guide / Registry: official description, default, options, source link, class.",
     "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "set_request", "description": "Record the user's request in plain language (stage 1), in the user's own words.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "set_draft", "description": "Propose design values. values maps field id (e.g. 'input:bl_pbl_physics') to {value, reason}. Per-domain fields take a comma-separated string such as '1,0,0'. Updates the Review page immediately; the user still presses Submit. Returns the compile check of the draft.",
     "parameters": {"type": "object", "properties": {
         "values": {"type": "object", "additionalProperties": {"type": "object", "properties": {
             "value": {"type": ["string", "number"]}, "reason": {"type": "string"}}, "required": ["value", "reason"]}},
         "note": {"type": "string"}}, "required": ["values"]}},
    {"name": "check_draft", "description": "Run the compile checks on the current draft without submitting.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "post_digest", "description": "Post a specialist handoff digest to the case board. Every key is required: key_findings (non-empty), claims_made, deviations_from_outline, open_questions, downstream_flags (empty list = nothing to report).",
     "parameters": {"type": "object", "properties": {
         "digest": {"type": "object", "properties": {
             "key_findings": {"type": "array"}, "claims_made": {"type": "array"},
             "deviations_from_outline": {"type": "array"}, "open_questions": {"type": "array"},
             "downstream_flags": {"type": "array"}},
             "required": ["key_findings", "claims_made", "deviations_from_outline", "open_questions",
                          "downstream_flags"]},
         "specialist": {"type": "string"}, "task": {"type": "string"}}, "required": ["digest"]}},
]

FUNCS = {"case_state": case_state, "design_fields": design_fields, "lookup_parameter": lookup_parameter,
         "set_request": set_request, "set_draft": set_draft, "check_draft": check_draft,
         "post_digest": post_digest}


def call(case: Path, name: str, args: dict):
    if name not in FUNCS:
        return {"error": f"unknown tool {name}"}
    return FUNCS[name](case, **(args or {}))
