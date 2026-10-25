"""dashboard — one HTML page per workflow stage, generated from the case directory.

Case directory::

    request.md            stage 1  (user)
    draft.yaml            stage 2  (agent: design values + reasons)
    experiment.yaml       stage 3  (user-approved specification, written by Submit)
    compile_report.yaml   stage 4  (compiler: derived fields + rule results)
    state.yaml            current stage, per-stage status, history
    dashboard/NN_<stage>.html, index.html

Pages are a view of these files; regenerate after any change (the server does this automatically).
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import yaml

from . import checks, render, runops

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_REGISTRY = REPO / "build" / "userguide" / "registry_fields.yaml"


def _y(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def _w(p: Path, data) -> None:
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def stages() -> list[dict]:
    return _y(HERE / "workflow.yaml")["stages"]


def page_name(i: int, sid: str) -> str:
    return f"{i + 1:02d}_{sid}.html"


# --------------------------------------------------------------------------- state


def case_write(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(case, *args, **kwargs):
        from .flow.state import writer
        with writer(case):
            return fn(case, *args, **kwargs)
    return wrapped


@case_write
def load_state(case: Path) -> dict:
    """Journal is authoritative. Legacy YAML is migrated once, conservatively to review.

    Legacy done/launch states have no bound approval, so must be re-reviewed.
    Options remain presentation-only data in state.yaml.
    """
    from .flow.state import Journal
    st = _y(case / "state.yaml") or {}
    j = Journal(case, start="request")
    if not j.rows():
        legacy = st.get("current")
        cur = legacy if legacy in ("request", "decompose", "review") else "review" if legacy else "request"
        if not legacy:
            cur = "review" if (case / "draft.yaml").exists() else "decompose" if (case / "request.md").exists() else "request"
        j.ensure_open(cur)
        if legacy:
            j.note("migration", f"legacy state.yaml {legacy} migrated to {cur}; approval requires Submit")
    ids = [s["id"] for s in stages()] + ["done", "failed"]
    statuses = {i: "pending" for i in ids}
    for row in j.rows():
        if row["kind"] == "transition":
            statuses[row["from"]] = "done" if row["outcome"] == "pass" else "failed"
    statuses[j.current()] = "failed" if j.current() == "failed" else "current"
    return {"current": j.current(), "status": statuses,
            "history": [{"time": r["at"], "event": r.get("note") or r.get("text") or r["kind"]} for r in j.rows()],
            "options": st.get("options", {}), "revision": j.revision}


def move(case, sid, note, outcome="pass"):
    from .flow.state import Journal
    j = Journal(case, start="request")
    j.ensure_open()
    if j.current() != sid:
        j.transition(sid, "user", outcome=outcome, note=note, expected_revision=j.revision)


def set_current(state: dict, sid: str, event: str, failed: str | None = None) -> None:
    ids = [s["id"] for s in stages()]
    for i in ids[: ids.index(sid)]:
        state["status"][i] = "done"   # a later pass clears an earlier failed mark
    for i in ids[ids.index(sid):]:
        state["status"][i] = "pending"
    if failed:
        state["status"][failed] = "failed"
    state["status"][sid] = "current"
    state["current"] = sid
    state["history"].append({"time": now(), "event": event})


@case_write
def init_case(case: Path, request: str | None = None, draft: Path | None = None) -> dict:
    case.mkdir(parents=True, exist_ok=True)
    if request is not None:
        (case / "request.md").write_text(request.strip() + "\n", encoding="utf-8")
    if draft is not None:
        (case / "draft.yaml").write_text(Path(draft).read_text(encoding="utf-8"), encoding="utf-8")
    state = load_state(case)
    _w(case / "state.yaml", state)
    return state


# --------------------------------------------------------------------------- submit (Review -> Compile)


def _same(a, b) -> bool:
    return str("" if a is None else a).replace(" ", "") == str("" if b is None else b).replace(" ", "")


@case_write
def save_review(case: Path, design: dict, field_map: Path) -> int:
    """Persist the user's form edits into draft.yaml (autosave). An edited field keeps the agent's value and
    reason as agent_value / reason and is marked edited_by: user; setting it back to the agent's value un-marks it."""
    ids = {k for k, v in _y(field_map)["fields"].items() if v.get("class") == "design"}
    draft = _y(case / "draft.yaml") or {"wrf_version": _y(field_map).get("version"), "design": {}}
    d = draft.setdefault("design", {}) or {}
    changed = 0
    for k, v in (design or {}).items():
        if k not in ids:
            continue
        cur = d.get(k)
        cur = cur if isinstance(cur, dict) else ({} if cur is None else {"value": cur})
        if _same(v, cur.get("value")):
            continue
        agent = cur.get("agent_value") if cur.get("edited_by") == "user" else cur.get("value")
        if cur.get("edited_by") == "user" and _same(v, agent):
            d[k] = {"value": agent, "reason": cur.get("reason", "")}
        else:
            d[k] = {"value": v, "reason": cur.get("reason", ""), "edited_by": "user", "agent_value": agent}
        changed += 1
    if changed:
        from .flow.actions import invalidate_approval
        invalidate_approval(case, "review edited")
        if load_state(case)["current"] not in ("request", "decompose", "review"):
            move(case, "review", "design edited; approval invalidated", outcome="fail")
        draft["design"] = d
        (case / "draft.yaml").write_text(
            "# Draft: agent proposals (value + reason); fields marked edited_by: user were changed in Review.\n"
            + yaml.safe_dump(draft, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        state = load_state(case)
        state["history"].append({"time": now(), "event": f"user edited {changed} field(s) in review (autosaved)"})
        _w(case / "state.yaml", state)
    return changed


@case_write
def approve(case: Path, field_map: Path, by: str = "user", design: dict | None = None,
            confirmation: str | None = None) -> dict:
    """The human approval step, shared by the review form and the CLI.

    ``design`` is the reviewed form; when it is None the CLI approves the selected draft as it stands. Approval
    always writes experiment.yaml plus approval.yaml (fingerprints of request/draft/discussion/spec) and runs the
    compile checks. Only ``by="user"`` is accepted, and a CLI approval must carry ``confirmation="approve"``.
    """
    from .flow.actions import approval_inputs, invalidate_approval
    if by != "user":
        raise ValueError("approval is reserved for the human review step")
    if design is None and confirmation != "approve":
        raise ValueError('CLI approval needs the literal confirmation "approve" (it is the human Submit)')
    discussion = _y(case / "discussion.yaml")
    draft = _y(case / "draft.yaml") or {}
    if discussion and (discussion.get("status") != "complete"
                       or draft.get("discussion_hash") != discussion.get("snapshot_hash")):
        raise ValueError("incomplete discussion or no human-selected draft")
    if design is not None:
        save_review(case, design, field_map)
    if load_state(case)["current"] != "review":
        raise ValueError("review is not the current stage")
    fm = _y(field_map)
    design_ids = [k for k, v in fm["fields"].items() if v.get("class") == "design"]
    draft = _y(case / "draft.yaml") or {}
    proposed = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (draft.get("design") or {}).items()}
    if design is None:
        design = {k: proposed[k] for k in design_ids if proposed.get(k) not in (None, "")}
    reasons = {k: v.get("reason") for k, v in (draft.get("design") or {}).items() if isinstance(v, dict)}
    spec = {
        "wrf_version": fm.get("version"),
        "approved_by": by,
        "approved_at": now(),
        "design": {k: design[k] for k in design_ids if k in design},
        "changed_from_draft": sorted(k for k, v in (draft.get("design") or {}).items()
                                     if isinstance(v, dict) and v.get("edited_by") == "user" and k in design),
        "agent_reasons": {k: r for k, r in reasons.items() if r},
        "discussion_hash": draft.get("discussion_hash"),
    }
    _w(case / "experiment.yaml", spec)
    inputs = approval_inputs(case)
    _w(case / "approval.yaml", {"valid": True, "inputs": inputs, "by": by})
    from .flow.state import Journal
    Journal(case, "request").note("user", f"approved specification {inputs['spec']}")
    report = checks.run(spec["design"], design_ids)
    report["checked_at"] = now()
    _w(case / "compile_report.yaml", report)
    if report["verdict"] == "PASS":
        move(case, "compile", "human approval")
    else:
        invalidate_approval(case, "compile failed at approval")
        move(case, "review", "compile failed at approval", "fail")
    state = load_state(case)
    _w(case / "state.yaml", state)
    return {"verdict": report["verdict"], "state": state,
            "failed": [r for r in report["rules"] if r["status"] == "FAIL"]}


@case_write
def submit(case: Path, design: dict, field_map: Path, by: str = "user") -> dict:
    """Review-form entry: same approval path, with the form's design values."""
    return approve(case, field_map, by=by, design=design)


@case_write
def back_to_review(case: Path) -> dict:
    from .flow.actions import invalidate_approval
    invalidate_approval(case, "human returned to review")
    move(case, "review", "human returned to review")
    state = load_state(case)
    set_current(state, "review", "returned to review")
    _w(case / "state.yaml", state)
    return state


# --------------------------------------------------------------------------- pages

_CSS = """*{box-sizing:border-box}body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f6f7f9;color:#222}
header{background:#1f3b57;color:#fff;padding:14px 22px}header small{opacity:.85}main{padding:18px 22px;max-width:1200px}
#top{position:sticky;top:0;z-index:30;box-shadow:0 2px 4px #0002}.stepper{display:flex;background:#0f2235;padding:0 22px;overflow-x:auto}
.stepper a{color:#cdd;padding:8px 12px;text-decoration:none;font-size:13px;border-bottom:3px solid transparent;white-space:nowrap}
.stepper a.done{color:#7ee08f}.stepper a.viewing{background:#ffffff22}.stepper a.current{color:#fff;border-bottom-color:#ffd166;font-weight:600}.stepper a.failed{color:#ff8a80}
.card{background:#fff;border-radius:8px;padding:12px 18px;margin-bottom:16px;box-shadow:0 1px 3px #0001}
table{border-collapse:collapse;width:100%;font-size:13px;table-layout:fixed}td,th{border-bottom:1px solid #eee;padding:5px 6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
.PASS{color:#2e7d32;font-weight:600}.FAIL{color:#c62828;font-weight:600}.WARN{color:#b26a00;font-weight:600}.DEFERRED{color:#607d8b;font-weight:600}
.big{font-size:22px}pre{background:#111;color:#cfc;padding:12px;border-radius:6px;white-space:pre-wrap}
button{padding:7px 14px;border:0;border-radius:4px;cursor:pointer;margin-right:8px}.go{background:#2e7d32;color:#fff}.back{background:#ffd166}
.muted{color:#777}"""


def stepper(state: dict, ui: dict, viewing: str | None = None) -> str:
    mark = {"done": "✓ ", "failed": "✗ ", "current": "● ", "pending": ""}
    items = []
    for i, s in enumerate(stages()):
        st = state["status"].get(s["id"], "pending")
        cls = st + (" viewing" if s["id"] == viewing else "")
        items.append(f'<a class="{cls}" href="{page_name(i, s["id"])}" title="{html.escape(ui.get("status_" + st, st))}">'
                     f'{mark[st]}{i + 1}. {html.escape(s["title"])}</a>')
    return '<nav class="stepper">' + "".join(items) + "</nav>"


def _stage_head(s: dict, i: int, state: dict, ui: dict) -> str:
    st = state["status"].get(s["id"], "pending")
    return (f'<div class="card"><div class="big">{ui.get("stage", "Stage")} {i + 1}: {html.escape(s["title"])} '
            f'<span class="{ {"done": "PASS", "current": "WARN", "failed": "FAIL"}.get(st, "DEFERRED") }">[{html.escape(ui.get("status_" + st, st))}]</span></div>'
            f'<p>{html.escape(s["summary"])}</p><p class="muted">{ui.get("owner", "Done by")}: <b>{s["owner"]}</b> · '
            f'{ui.get("gate", "Gate")}: {html.escape(s["gate"])} · {ui.get("artefacts", "Produces")}: {", ".join(map(html.escape, s["artefacts"]))}</p></div>')


def _page(title: str, nav: str, body: str, version: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{_CSS}</style></head>'
            f'<body><div id="top">{nav}<header><b>WRF {html.escape(str(version))} · {html.escape(title)}</b></header></div><main>{body}</main>'
            '<script>async function post(u){try{const r=await fetch(u,{method:"POST"});const j=await r.json();location.href=j.next;}'
            'catch(e){alert("The dashboard server is not running: python3 -m src.userguide serve <case>");}}</script></body></html>')


def _yaml_card(title: str, p: Path, ui: dict, reached: bool = False) -> str:
    if not p.exists():
        # an artefact missing from a stage that has already been passed is a defect, not "not reached yet"
        msg = ui.get("not_reached") if not reached else f"{p.name} is missing (the stage has been passed)"
        return f'<div class="card"><h3>{html.escape(title)}</h3><p class="muted">{html.escape(str(msg))}</p></div>'
    return f'<div class="card"><h3>{html.escape(title)} <small class="muted">{p.name}</small></h3><pre>{html.escape(p.read_text(encoding="utf-8"))}</pre></div>'


def _compile_body(case: Path, ui: dict, dic: dict) -> str:
    rep = _y(case / "compile_report.yaml")
    if not rep:
        return f'<div class="card"><p class="muted">{ui.get("not_reached")}</p></div>'
    rules = dic.get("rules", {})
    rows = "".join(
        f'<tr><td><code>{html.escape(r["rule"])}</code><br><small>{html.escape(rules.get(r["rule"], ""))}</small></td>'
        f'<td class="{r["status"]}">{r["status"]}</td><td>{html.escape(", ".join(r["fields"]))}</td><td>{html.escape(str(r["detail"]))}</td></tr>'
        for r in rep["rules"])
    der = "".join(f"<tr><td><code>{html.escape(k)}</code></td><td>{html.escape(str(v))}</td></tr>" for k, v in rep["derived"].items())
    btn = (f'<button class="back" onclick="post(\'/api/back\')">{ui.get("back_review")}</button>' if rep["verdict"] == "FAIL"
           else f'<a href="{page_name(4, "launch")}"><button class="go">{ui.get("proceed")} →</button></a>')
    return (f'<div class="card"><span class="big">{ui.get("verdict")}: <span class="{rep["verdict"]}">{rep["verdict"]}</span></span> '
            f'<small class="muted">{html.escape(str(rep.get("checked_at", "")))}</small><p>{btn}</p></div>'
            f'<div class="card"><h3>{ui.get("rules_title")}</h3><table><tr><th style="width:34%">{ui.get("col_rule")}</th><th style="width:9%">{ui.get("col_status")}</th>'
            f'<th style="width:22%">{ui.get("col_fields")}</th><th>{ui.get("col_detail")}</th></tr>{rows}</table></div>'
            f'<div class="card"><h3>{ui.get("derived_title")}</h3><table>{der}</table></div>')


def _file_card(title: str, p: Path, ui: dict) -> str:
    if not p.exists():
        return ""
    return f'<div class="card"><h3>{html.escape(title)} <small class="muted">{p.name}</small></h3><pre>{html.escape(p.read_text(encoding="utf-8"))}</pre></div>'


def _path_form(case: Path, state: dict, key: str, api: str, ui: dict, extra: str = "") -> str:
    val = html.escape(str((state.get("options", {}).get(key) or {}).get("path", case / "run")))
    btn = {"monitor": "survey", "verify": "verify_run", "restart": "restart_from"}[key]
    return (f'<div class="card"><label>{ui.get("run_dir_label", "Run directory")} '
            f'<input id="p-{key}" value="{val}" style="width:70%"></label> {extra} '
            f'<button class="go" onclick="postjson(\'{api}\',\'{key}\')">{ui.get(btn, btn)}</button></div>'
            '<script>async function postjson(u,k){const q={};document.querySelectorAll("#p-"+k+" , #p-"+k+" ~ label input, #p-"+k+" ~ * input").forEach(i=>{if(i.name)q[i.name]=i.value;});'
            'q.path=document.querySelector("#p-"+k).value;'
            "const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(q)});"
            "if(!r.ok)alert(await r.text());else location.reload();}</script>")


def _opt(state: dict, key: str) -> dict:
    return (state.get("options") or {}).get(key) or {}


def _age(mins) -> str:
    if mins is None:
        return "?"
    if mins > 30 * 24 * 60:
        return f"~{mins / (30 * 24 * 60):.0f} months ago"
    if mins > 1440:
        return f"~{mins / 1440:.1f} days ago"
    return f"{mins} min ago"


def _survey_card(case: Path, state: dict, ui: dict) -> str:
    o = _opt(state, "monitor")
    if not o.get("result"):
        return ""
    r = o["result"]
    rows = "".join(f"<tr><td>{html.escape(d)}</td><td>{v['frames']}</td><td>{html.escape(str(v['first']))}</td>"
                   f"<td>{html.escape(str(v['last']))}</td><td>{html.escape(str(v['interval_min']))}</td><td>{v['gaps']}</td></tr>"
                   for d, v in r["domains"].items())
    rst = "; ".join(f"{d}: {len(t)} file(s), latest {t[-1]}" for d, t in (r.get("restart_files") or {}).items()) or "none"
    return (f'<div class="card"><h3>{ui.get("survey_title", "Survey")}</h3>'
            f'<p><span class="big"><span class="{ "PASS" if r["rsl_success"] else "WARN" }">{html.escape(r["state"])}</span></span>'
            f' · rsl SUCCESS: {r["rsl_success"]} · newest file {_age(r["newest_file_age_min"])}</p>' 
            f'<table><tr><th>domain</th><th>frames</th><th>first</th><th>last</th><th>interval (min)</th><th>gaps</th></tr>{rows}</table>'
            f'<p class="muted">restart files: {html.escape(rst)}</p></div>')


def _verify_card(case: Path, state: dict, ui: dict) -> str:
    o = _opt(state, "verify")
    if not o.get("result"):
        return ""
    r = o["result"]
    rows = "".join(f'<tr><td>{html.escape(c["check"])}</td><td class="{"PASS" if c["ok"] else "FAIL"}">'
                   f'{"PASS" if c["ok"] else "FAIL"}</td><td>{html.escape(str(c.get("detail", "")))}</td></tr>'
                   for c in r["checks"])
    return (f'<div class="card"><h3>{ui.get("verify_title", "Verify")}</h3>'
            f'<p><span class="big"><span class="{r["verdict"]}">{r["verdict"]}</span></span> <small class="muted">{html.escape(r["run_dir"])}</small></p>'
            f'<table><tr><th>{ui.get("col_rule", "Check")}</th><th>{ui.get("col_status", "Status")}</th><th>{ui.get("col_detail", "Detail")}</th></tr>{rows}</table></div>')


DECOMPOSE_JS = """
async function loadDiscussion(){
 const box=document.getElementById('disc');if(!box)return;
 let j={};try{j=await (await fetch('/api/discussion')).json();}catch(e){box.textContent='cannot read the discussion';return;}
 if(j.running){box.innerHTML='<div class="warn">Discussion is running ('+(j.model||'')+') — this page reloads when it finishes.</div>';setTimeout(loadDiscussion,5000);return;}
 if(!j.exists){box.innerHTML='<div class="warn">Stage 2 work has not started. Run the discussion: three experts answer the request independently, cross-review each other, and the synthesis keeps their disagreements.<br><button id="start">Run the discussion</button></div>';
  const b=document.getElementById('start');if(b)b.onclick=async()=>{b.disabled=true;b.textContent='starting…';await fetch('/api/discuss',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});loadDiscussion();};return;}
 const alts=(j.alternatives||[]).map(a=>'<li><b>'+a.id+'</b>'+(a.selected?' <span class="muted">(selected)</span>':'')+'<ul>'+(a.tradeoffs||[]).map(t=>'<li>'+t+'</li>').join('')+'</ul>'+(a.selected?'':'<button class="pick" data-alt="'+a.id+'">Select '+a.id+'</button>')+'</li>').join('');
 const list=a=>a&&a.length?'<ul>'+a.map(x=>'<li>'+(typeof x==='string'?x:JSON.stringify(x))+'</li>').join('')+'</ul>':'<p class="muted">none</p>';
 box.innerHTML='<p class="muted">Status: <b>'+(j.status||'none')+'</b>'+(j.model?' · '+j.model:'')+(j.selected?' · selected '+j.selected:'')+'. Selecting writes the draft; Submit in Review is still yours.</p>'+
  '<h4>Alternatives</h4><ul>'+(alts||'<li class="muted">none</li>')+'</ul>'+
  '<h4>Disagreements kept</h4>'+list(j.disagreements)+
  '<h4>Questions for you</h4>'+list(j.open_questions)+
  (j.error?'<div class="warn">'+j.error+'</div>':'');
 box.querySelectorAll('button.pick').forEach(b=>b.onclick=async()=>{b.disabled=true;
  const r=await fetch('/api/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({alternative:b.dataset.alt})});
  if(!r.ok){box.insertAdjacentHTML('afterbegin','<div class="warn">'+await r.text()+'</div>');b.disabled=false;return;}
  location.reload();});
}
loadDiscussion();
"""


def _decompose_body(case: Path, state: dict, ui: dict) -> str:
    """Stage 2 is where the experts work; this page is their workspace, not the approval form."""
    request = (case / "request.md").read_text(encoding="utf-8") if (case / "request.md").exists() else ""
    draft = _y(case / "draft.yaml") or {}
    picked = draft.get("selected_alternative")
    note = (f"<p class=\"muted\">Draft written from alternative <b>{html.escape(str(picked))}</b>. "
            f"Continue to Review to approve the values.</p>" if picked else "")
    return (f'<div class="card"><h3>Request</h3><pre style="white-space:pre-wrap">{html.escape(request)}</pre></div>'
            f'<div class="card"><h3>Expert discussion</h3>{note}<div id="disc" class="muted">loading…</div></div>'
            f'<script>{DECOMPOSE_JS}</script>')


def _launch_body(case: Path, ui: dict) -> str:
    manifest = case / "build" / "emit_manifest.yaml"
    btn = (f'<button class="go" onclick="post(\'/api/emit\')">{ui.get("emit", "Compile to namelists")}</button> '
           f'<button class="go" onclick="post(\'/api/prepare\')">{ui.get("prepare", "Prepare run directory")}</button>')
    if manifest.exists():
        btn = f'<button class="go" onclick="post(\'/api/emit\')">{ui.get("emit_again", "Re-generate namelists")}</button>'
    if not manifest.exists():
        return (f'<div class="card"><p>{ui.get("emit_intro", "Emit namelist.wps / namelist.input from the approved specification.")}</p>'
                f'<p>{btn}</p></div>')
    m = _y(manifest) or {}
    cards = ""
    for f in ("namelist.wps", "namelist.input"):
        p = case / "build" / f
        if p.exists():
            cards += (f'<div class="card"><h3>{f} <small class="muted">{m.get("files", {}).get(f, {}).get("sha256", "")[:12]}…</small></h3>'
                      f'<pre>{html.escape(p.read_text(encoding="utf-8"))}</pre></div>')
    return (f'<div class="card"><span class="big">{ui.get("emit_verdict", "Emit")}: '
            f'<span class="{m.get("verdict", "")}">{m.get("verdict", "?")}</span></span><p>{btn}</p>'
            + (f'<p class="muted">missing: {html.escape(", ".join(m.get("missing_required", [])))}</p>' if m.get("missing_required") else "")
            + (f'<p class="muted">unplaced spec keys: {html.escape(", ".join(m.get("unplaced_spec_keys", [])))}</p>' if m.get("unplaced_spec_keys") else "")
            + '</div>' + cards)


@case_write
def build(case: Path, field_map: Path | None = None, dictionary: Path | None = None,
          registry: Path | None = None, lang: str | None = None) -> Path:
    field_map = field_map or HERE / "field_map.yaml"
    dictionary = dictionary or HERE / "dictionary.yaml"
    registry = registry or DEFAULT_REGISTRY
    dic = render.load_dictionary(dictionary, lang) or {}
    ui = dic.get("ui", {})
    state = load_state(case)
    _w(case / "state.yaml", state)
    fm = _y(field_map)
    version = fm.get("version")
    out = case / "dashboard"
    out.mkdir(parents=True, exist_ok=True)
    hist = "".join(f"<tr><td style='width:28%'>{html.escape(h['time'])}</td><td>{html.escape(h['event'])}</td></tr>" for h in state["history"])
    history = f'<div class="card"><h3>{ui.get("history")}</h3><table>{hist}</table></div>'

    for i, s in enumerate(stages()):
        sid = s["id"]
        head = _stage_head(s, i, state, ui)
        nav = stepper(state, ui, viewing=sid)
        if sid == "decompose":
            body = head + _decompose_body(case, state, ui)
        elif sid == "review":
            prefill = case / "draft.yaml"
            merged = render.merge(_y(registry), fm, dic, _y(prefill))
            # keep the agent's reasons visible even after approval
            draft = _y(case / "draft.yaml") or {}
            for f in merged["fields"]:
                dv = (draft.get("design") or {}).get(f["id"])
                if f.get("prefill") and isinstance(dv, dict) and dv.get("reason"):
                    f["prefill"]["reason"] = dv["reason"]
            page = render.render_html(merged, stagenav=nav, submit=state["current"] in ("review", "compile"))
            (out / page_name(i, sid)).write_text(page, encoding="utf-8")
            continue
        if sid == "request":
            p = case / "request.md"
            body = head + (f'<div class="card"><h3>{ui.get("request_title")}</h3><pre>{html.escape(p.read_text(encoding="utf-8"))}</pre></div>'
                           if p.exists() else "") + history
        elif sid == "decompose":
            body = head + _decompose_body(case, state, ui) + _yaml_card(
                "Agent draft", case / "draft.yaml", ui, reached=state["status"].get("decompose") != "pending")
        elif sid == "compile":
            body = head + _compile_body(case, ui, dic) + _yaml_card(ui.get("approved", "Approved"), case / "experiment.yaml", ui)
        elif sid == "launch":
            body = head + _launch_body(case, ui) + _yaml_card("Emit manifest", case / "build" / "emit_manifest.yaml", ui) \
                + _yaml_card("Run manifest", case / "run" / "run_manifest.yaml", ui) \
                + _file_card("Allrun", case / "run" / "Allrun", ui)
        elif sid == "monitor":
            body = head + _path_form(case, state, "monitor", "/api/survey", ui, extra="") + _survey_card(case, state, ui)
        elif sid == "verify":
            body = head + _path_form(case, state, "verify", "/api/verify", ui,
                                     extra='<label>expected frames <input name="expect_frames" value="%s" size="6"></label>'
                                           '<label>interval (min) <input name="interval_min" value="%s" size="6"></label>'
                                           % (html.escape(str((state.get("options", {}).get("verify") or {}).get("expect_frames", ""))),
                                              html.escape(str((state.get("options", {}).get("verify") or {}).get("interval_min", ""))))) \
                + _verify_card(case, state, ui)
        elif sid == "restart":
            body = head + _path_form(case, state, "restart", "/api/restart", ui, extra="") + _yaml_card("Restart spec", case / "restart.yaml", ui)
        else:
            body = head + f'<div class="card"><p class="muted">{ui.get("not_reached") if state["status"].get(sid) == "pending" else ""}</p></div>'
        (out / page_name(i, sid)).write_text(_page(s["title"], nav, body, version), encoding="utf-8")

    ids = [s["id"] for s in stages()]
    if state["current"] in ids:
        cur, target = ids.index(state["current"]), page_name(ids.index(state["current"]), state["current"])
    else:  # a terminal stage outside the rendered 8 (done / failed): show the last page, state stays visible
        cur, target = len(ids) - 1, page_name(len(ids) - 1, ids[-1])
    (out / "index.html").write_text(f'<!doctype html><meta http-equiv="refresh" content="0; url={target}"><a href="{target}">{target}</a>', encoding="utf-8")
    return out / "index.html"
