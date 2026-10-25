"""render — registry_fields.yaml ⊕ field_map.yaml ⊕ dictionary.yaml (⊕ prefill) → self-contained HTML.

* "Experiment setup": only class=design fields (what the compiler cannot decide, so a person must).
  Each field shows the dictionary question, options, the Users' Guide / README.namelist original text
  with source links, and the compile rules that will be checked. Agent suggestions (prefill) are shown
  inline. The experiment YAML is exported in the browser (no server).
* "All parameters": every namelist field from the Registry, searchable, labelled
  design / derived / runtime / unmapped — the Users' Guide as a browsable parameter table.
  Works without field_map (everything is then "unmapped").

One-way derivation: the HTML is a view. Change field_map.yaml / dictionary.yaml and regenerate.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import yaml

FILE_KEY = {"input": "namelist.input", "wps": "namelist.wps"}
KEY_OF = {v: k for k, v in FILE_KEY.items()}


def _fid(rec: dict) -> str:
    return f"{KEY_OF.get(rec['file'], rec['file'])}:{rec['name']}"


def merge(registry: dict, field_map: dict | None, dictionary: dict | None = None,
          prefill: dict | None = None) -> dict:
    fm_all = field_map or {}
    fm = fm_all.get("fields", {}) or {}
    dic = dictionary or {}
    dfields = dic.get("fields", {}) or {}
    rules_txt = dic.get("rules", {}) or {}
    src_txt = dic.get("sources", {}) or {}
    pf = (prefill or {}).get("values") or (prefill or {}).get("design") or {}
    pf = {k: (v if isinstance(v, dict) else {"value": v}) for k, v in pf.items()}
    by_id = {_fid(r): r for r in registry["fields"]}

    problems = [f"field_map key {k} is not in the parameter registry" for k in fm if k not in by_id]
    problems += [f"dictionary entry {k} has no field_map entry" for k in dfields if k not in fm]
    problems += [f"prefill {k} is not a design field" for k in pf if (fm.get(k) or {}).get("class") != "design"]
    for k, a in fm.items():
        for r in a.get("rules") or []:
            if r not in rules_txt:
                problems.append(f"rule id {r} ({k}) missing from dictionary.rules")
        if a.get("class") == "design" and not (dfields.get(k) or {}).get("question"):
            problems.append(f"design field {k} has no dictionary question")
        if a.get("group") and a["group"] not in (dic.get("groups") or {}):
            problems.append(f"group {a['group']} missing from dictionary.groups")

    order = {k: i for i, k in enumerate(fm)}
    out = []
    for fid, rec in by_id.items():
        ann = fm.get(fid, {})
        txt = dfields.get(fid, {})
        ug = rec.get("ug") or {}
        rm = rec.get("readme") or {}
        upstream = ug.get("options") or rm.get("options") or []
        if not upstream and ann.get("values"):
            upstream = [{"value": str(v), "label": ""} for v in ann["values"]]
        opt_txt = {str(k): v for k, v in (txt.get("options") or {}).items()}
        options = [{"value": o["value"], "label": opt_txt.get(str(o["value"])) or o["label"] or str(o["value"]),
                    "official": o["label"]} for o in upstream]
        derive = [src_txt.get(s, s) for s in (ann.get("derive_from") or [])]
        out.append({
            "id": fid, "name": rec["name"], "file": rec["file"], "block": rec["block"],
            "type": rec.get("type"), "default": rec.get("default"), "per_domain": rec.get("per_domain"),
            "class": ann.get("class", "unmapped"), "group": ann.get("group"),
            "label": txt.get("label"), "question": txt.get("question"), "note": txt.get("note"),
            "unit": ann.get("unit") or (rec.get("registry") or {}).get("units") or None,
            "widget": ann.get("widget"), "placeholder": ann.get("placeholder"),
            "per_domain_hint": ann.get("per_domain_hint", False), "show_if": ann.get("show_if"),
            "rules": [rules_txt.get(r, r) for r in ann.get("rules") or []],
            "derive_from": derive, "options": options,
            "desc_official": ug.get("desc") or rm.get("desc") or (rec.get("registry") or {}).get("descrip") or "",
            "ug_url": ug.get("url"), "readme_line": rm.get("line"),
            "registry_file": (rec.get("registry") or {}).get("file"),
            "prefill": pf.get(fid), "order": order.get(fid),
        })
    out.sort(key=lambda f: (f["file"], f["block"] or "", f["name"]))
    counts = {c: sum(1 for f in out if f["class"] == c) for c in ("design", "derived", "runtime", "unmapped")}
    groups = [{"id": g, "title": (dic.get("groups") or {}).get(g, g)} for g in fm_all.get("groups", [])]
    return {"fields": out, "groups": groups, "counts": counts, "problems": problems,
            "ui": dic.get("ui", {}), "version": fm_all.get("version") or registry.get("generated_from")}


_TEMPLATE = r"""<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:#f6f7f9;color:#222}
#top{position:sticky;top:0;z-index:30;box-shadow:0 2px 4px #0002}
header{background:#1f3b57;color:#fff;padding:8px 22px}header small{opacity:.85}
#saved{font-size:12px;opacity:.85;margin-left:10px}
nav button{margin-right:8px;padding:6px 14px;border:0;border-radius:4px;cursor:pointer}
nav button.on{background:#ffd166}main{padding:14px 22px;display:flex;gap:18px;align-items:flex-start}
#content{flex:1;min-width:0;max-width:1000px;overflow-x:hidden}
#toc{flex:0 0 220px;position:sticky;top:calc(var(--top,120px) + 12px);max-height:calc(100vh - var(--top,120px) - 24px);overflow:auto;background:#fff;border-radius:8px;padding:8px 6px;box-shadow:0 1px 3px #0001;font-size:12.5px}
#toc .tg{font-weight:600;color:#1f3b57;margin:8px 6px 2px}#toc .tg:first-child{margin-top:0}
#toc a{display:flex;gap:6px;align-items:center;padding:3px 6px;border-radius:4px;color:#333;text-decoration:none;cursor:pointer}
#toc a:hover{background:#eef2f6}#toc a.on{background:#ffd16655;font-weight:600}#toc a.off{opacity:.4}
.dot{flex:0 0 8px;height:8px;border-radius:50%;border:1.5px solid #9aa}.dot.fill{background:#4caf50;border-color:#4caf50}.dot.mine{background:#ff9800;border-color:#ff9800}
main.toc-off #toc{display:none}main.toc-off #content{max-width:1100px}
#tocbtn{background:#ffffff33;color:#fff}#tocbtn.on{background:#ffd166;color:#222}
.f,.grp{scroll-margin-top:calc(var(--top,120px) + 12px)}.f.flash{animation:fl 1.2s}@keyframes fl{from{background:#fff3b0}to{background:transparent}}
.sug.mine{background:#fff4e5;border-left-color:#ff9800}

.grp{background:#fff;border-radius:8px;padding:12px 18px;margin-bottom:16px;box-shadow:0 1px 3px #0001}
.f{border-top:1px solid #eee;padding:10px 0}.f:first-of-type{border-top:0}
.q{font-weight:600}.lab{color:#666;font-size:12px;margin-left:6px;font-weight:400}
.f input,.f select{margin-top:6px;padding:5px;width:420px;max-width:100%}
.sug{background:#eef7ee;border-left:3px solid #4caf50;padding:4px 8px;margin-top:6px;font-size:13px}
details{font-size:12.5px;color:#444;margin-top:6px;overflow-wrap:anywhere}code{background:#f0f0f0;padding:0 3px}
.rule{color:#8a4b00}.warn{background:#fff3cd;padding:8px;border-radius:4px;margin-bottom:12px}
table{border-collapse:collapse;width:100%;font-size:12.5px;background:#fff;table-layout:fixed}
td,th{border-bottom:1px solid #eee;padding:4px 6px;vertical-align:top;text-align:left;overflow-wrap:anywhere}
th:nth-child(1){width:16%}th:nth-child(2){width:13%}th:nth-child(3){width:13%}th:nth-child(4){width:8%}
.b{font-size:11px;padding:1px 6px;border-radius:8px;color:#fff}
.b.design{background:#c0392b}.b.derived{background:#2e86c1}.b.runtime{background:#7f8c8d}.b.unmapped{background:#bbb}
pre{background:#111;color:#cfc;padding:12px;border-radius:6px;white-space:pre-wrap}
.stepper{display:flex;gap:0;background:#0f2235;padding:0 22px;overflow-x:auto}.stepper a{cursor:pointer}
.stepper a{color:#cdd;padding:8px 12px;text-decoration:none;font-size:13px;border-bottom:3px solid transparent;white-space:nowrap}
.stepper a.done{color:#7ee08f}.stepper a.viewing{background:#ffffff22}.stepper a.current{color:#fff;border-bottom-color:#ffd166;font-weight:600}.stepper a.failed{color:#ff8a80}
#submit{background:#2e7d32;color:#fff;font-weight:600}
</style></head><body><div id="top">__STAGENAV__
<header><b id="h-title"></b> <small id="h-sub"></small><span id="saved"></span>
<div><small id="h-counts"></small></div>
<nav style="margin-top:6px"><button id="tocbtn" title="Show / hide the field index"></button><button class="on" data-t="form"></button><button data-t="all"></button><button data-t="out"></button>__SUBMIT__</nav></header></div>
<main><aside id="toc"></aside><div id="content">
<div id="problems"></div>
<section id="form"></section>
<section id="all" hidden><input id="q" style="width:360px;max-width:100%;padding:6px;margin-bottom:8px">
<table><thead><tr><th></th><th></th><th></th><th></th><th></th></tr></thead><tbody id="tb"></tbody></table></section>
<section id="out" hidden><p id="out-intro"></p><button id="dl"></button> <button id="cp"></button><pre id="yaml"></pre></section>
</div></main>
<script>
const LANG=new URLSearchParams(location.search).get('lang');
if(LANG)document.querySelectorAll('.stepper a').forEach(a=>a.setAttribute('href',a.getAttribute('href')+'?lang='+LANG));
const D=__DATA__;
const sb=$('#submit');if(sb){sb.textContent=T('submit');sb.onclick=async()=>{sb.disabled=true;sb.textContent=T('submitting');
 const spec={};for(const f of design_fields()){if(val[f.id])spec[f.id]=val[f.id];}
 try{const r=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({design:spec})});
  if(!r.ok)throw new Error(await r.text());const j=await r.json();location.href=j.next;}
 catch(e){if(location.protocol==='file:'){$('#dl').click();alert(T('submit_offline'));}else alert(e);sb.disabled=false;sb.textContent=T('submit');}};}
function design_fields(){return design.filter(f=>{const el=document.querySelector(`[data-f="${f.id}"]`);return !(el&&el.style.display==='none');});}
const mine=new Set(design.filter(f=>f.prefill&&f.prefill.edited_by==='user').map(f=>f.id));
function setTop(){document.documentElement.style.setProperty('--top',$('#top').offsetHeight+'px');}
addEventListener('resize',setTop);
function buildToc(){let h='';const groups=D.groups.length?D.groups:[{id:null,title:''}];
 for(const g of groups){const fs=design.filter(f=>(f.group||null)===g.id);if(!fs.length)continue;h+=`<div class="tg">${esc(g.title)}</div>`;
  for(const f of fs)h+=`<a data-go="${f.id}"><span class="dot"></span><span>${esc(f.label||f.name)}</span></a>`;}
 $('#toc').innerHTML=h;document.querySelectorAll('#toc a').forEach(a=>a.onclick=()=>{const el=document.querySelector(`[data-f="${a.dataset.go}"]`);if(!el)return;
  scrollTo({top:el.getBoundingClientRect().top+scrollY-$('#top').offsetHeight-12,behavior:'instant'});spy();el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');const c=el.querySelector('[data-id]');if(c)setTimeout(()=>c.focus({preventScroll:true}),400);});tocState();}
function tocState(){document.querySelectorAll('#toc a').forEach(a=>{const id=a.dataset.go,d=a.querySelector('.dot'),el=document.querySelector(`[data-f="${id}"]`);
 d.className='dot'+(mine.has(id)?' mine':(val[id]?' fill':''));a.classList.toggle('off',!!el&&el.style.display==='none');});}
function spy(){const top=$('#top').offsetHeight+20;let cur=null;for(const f of design){const el=document.querySelector(`[data-f="${f.id}"]`);if(!el||el.style.display==='none')continue;
 if(el.getBoundingClientRect().top-top<=0)cur=f.id;else{if(!cur)cur=f.id;break;}}
 document.querySelectorAll('#toc a').forEach(a=>{const on=a.dataset.go===cur;a.classList.toggle('on',on);if(on)a.scrollIntoView({block:'nearest'});});}
addEventListener('scroll',()=>requestAnimationFrame(spy),{passive:true});
// ---- autosave: debounced after edits, and always before leaving the page (step switch, reload, close)
const online=location.protocol!=='file:'&&!!sb;let timer=null;
function payload(){const spec={};for(const f of design)spec[f.id]=val[f.id]??'';return JSON.stringify({design:spec});}
function mark(t){$('#saved').textContent=t;}
async function save(){clearTimeout(timer);if(!online||!window.__dirty)return;mark(T('saving'));
 try{const r=await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:payload()});const j=await r.json();
  window.__selfVersion=j.version;window.__dirty=false;mark('✓ '+T('saved')+' '+new Date().toLocaleTimeString());}catch(e){mark('⚠ '+e);}}
function schedule(){if(!online)return;clearTimeout(timer);mark('…');timer=setTimeout(save,1500);}
addEventListener('pagehide',()=>{if(online&&window.__dirty)navigator.sendBeacon('/api/save',new Blob([payload()],{type:'application/json'}));});
document.querySelectorAll('.stepper a').forEach(a=>a.onclick=async e=>{e.preventDefault();await save();location.href=a.getAttribute('href');});
setToc(localStorage.getItem('wrfAgentToc')?localStorage.getItem('wrfAgentToc')==='0':innerWidth<760);
renderForm();renderAll();setTop();spy();
</script></body></html>
"""


def render_html(merged: dict, stagenav: str = "", submit: bool = False) -> str:
    data = json.dumps(merged, ensure_ascii=False).replace("</", "<\\/")
    title = (merged["ui"].get("title") or "WRF {version} parameter setup").replace("{version}", str(merged["version"]))
    return (_TEMPLATE.replace("__DATA__", data).replace("__TITLE__", html.escape(title)).replace("__LANG__", html.escape(str(merged.get("language", "en"))))
            .replace("__STAGENAV__", stagenav).replace("__SUBMIT__", '<button id="submit"></button>' if submit else ""))


def _load(p: Path | None):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p and p.exists() else None

def load_dictionary(path: Path | None, lang: str | None = None) -> dict | None:
    """English dictionary + optional per-language overlay: dictionary.<lang>.yaml overrides keys in place.
    Adding a language never touches code or the English file."""
    base = _load(path)
    if base and lang and lang != base.get("language", "en"):
        ov = path.with_suffix(f".{lang}.yaml")  # dictionary.yaml -> dictionary.zh.yaml
        if ov.exists():
            import copy
            base = copy.deepcopy(base)
            for k, v in yaml.safe_load(ov.read_text(encoding="utf-8")).items():
                if isinstance(v, dict) and isinstance(base.get(k), dict):
                    base[k].update(v)
                else:
                    base[k] = v
            base["language"] = lang
    return base


def render(registry_yaml: Path, field_map_yaml: Path | None, out_html: Path,
           prefill_yaml: Path | None = None, dictionary_yaml: Path | None = None,
           lang: str | None = None) -> dict:
    merged = merge(_load(registry_yaml), _load(field_map_yaml), load_dictionary(dictionary_yaml, lang), _load(prefill_yaml))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(merged), encoding="utf-8")
    return {"counts": merged["counts"], "problems": merged["problems"], "out": str(out_html)}
