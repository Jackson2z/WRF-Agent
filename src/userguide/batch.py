"""batch — design-matrix expansion: base specification × axes -> member bundles (pure backend).

A batch is declared in ``batch.yaml`` inside the case directory::

    base: experiment.yaml          # approved specification (path relative to the case, or omitted -> that)
    urbparm_base: /path/URBPARM.TBL   # required iff any axis targets URBPARM
    axes:
      pbl:  {target: "input:bl_pbl_physics", values: [1, 8]}     # namelist axis (a design field id)
      albr: {target: "urbparm:ALBR", values: [0.12, 0.20, 0.60]} # URBPARM axis (3-class, all classes set)
      albb: {target: "urbparm:ALBB", values: [0.12, 0.20, 0.60]}

Members are the cartesian product. For each member the batch writes::

    members/<mid>/experiment.yaml      base design + namelist-axis overrides (reasons point at the axis)
    members/<mid>/build/namelist.*     emitted by the same compiler as single runs
    members/<mid>/build/URBPARM.TBL    base table with the axis keys rewritten (when urbparm axes exist)
    members/<mid>/manifest.yaml        axis values + per-artifact sha256 + emit verdict

and a case-level ``batch_manifest.yaml``. Two constructional guarantees are checked, not assumed:

* design dicts of any two members differ only on declared namelist axis keys;
* URBPARM tables of any two members differ only on declared URBPARM keys.

URBPARM editing reuses ``src/WRF_Simulation/urbparm_modifier.set_3class`` (3-class USGS, all classes set
to the axis value). The C01 Singapore study is exactly this shape: 3 roof × 3 wall albedo × 2 PBL = 18.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
from pathlib import Path

import yaml

from . import emit

URBPARM_KEYS = {"ALBR", "ALBB", "ALBG", "AH", "HIURT"}


def _y(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p and p.exists() else None


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _set_urbparm(content: str, key: str, value: float) -> str:
    from ..WRF_Simulation.urbparm_modifier import set_3class
    return set_3class(content, key, value)


def _urbparm_keys(tbl: str) -> dict[str, str]:
    """Extract the ``KEY: v1, v2, v3`` lines we may perturb, for the only-declared-keys check."""
    import re
    return {m.group(1): m.group(0) for m in re.finditer(r"^(ALB[RGB]|AH):\s+[-0-9., ]+$", tbl, re.M)}


def expand(case: Path, batch_yaml: Path | None = None, out_members: Path | None = None) -> dict:
    case = Path(case)
    b = _y(batch_yaml or case / "batch.yaml")
    if not b or not b.get("axes"):
        return {"ok": False, "error": "batch.yaml missing or has no axes"}
    axes: dict[str, dict] = b["axes"]
    base_path = case / b.get("base", "experiment.yaml")
    base = _y(base_path)
    if not base or not base.get("design"):
        return {"ok": False, "error": f"base specification not found: {base_path}"}
    urb_axes = {k: a for k, a in axes.items() if str(a["target"]).startswith("urbparm:")}
    nl_axes = {k: a for k, a in axes.items() if k not in urb_axes}
    tbl_base = None
    if urb_axes:
        tb = Path(b["urbparm_base"] or "")
        if not tb.is_absolute():
            tb = case / tb
        if not tb.exists():
            return {"ok": False, "error": "urbparm axes need urbparm_base (path to a URBPARM.TBL)"}
        tbl_base = tb.read_text(encoding="utf-8", errors="replace")
        for a in urb_axes.values():
            if a["target"].split(":", 1)[1] not in _urbparm_keys(tbl_base):
                return {"ok": False, "error": f"urbparm_base has no '{a['target'].split(':', 1)[1]}:' 3-class entry"}

    members_dir = out_members or case / "members"
    names = sorted(axes)
    combos = list(itertools.product(*[axes[n]["values"] for n in names]))
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "base": {"path": str(base_path), "spec_sha256": _sha(base_path)},
        "urbparm_base": {"path": str(b.get("urbparm_base"))} if tbl_base else None,
        "axes": {k: axes[k] for k in names},
        "members": [],
    }
    designs: dict[str, dict] = {}
    tables: dict[str, dict] = {}
    for combo in combos:
        vals = dict(zip(names, combo))
        mid = "_".join(f"{n}{v}".replace(".", "p") for n, v in vals.items())
        mdir = members_dir / mid
        spec = {"wrf_version": base.get("wrf_version"), "forcing": base.get("forcing"),
                "design": {k: dict(v) if isinstance(v, dict) else {"value": v} for k, v in base["design"].items()},
                "batch": {"member": mid, "axis_values": {k: vals[k] for k in names if k in nl_axes}}}
        for n, v in vals.items():
            if n in nl_axes:
                ax = nl_axes[n]
                spec["design"][ax["target"]] = {"value": v, "reason": f"batch axis '{n}' = {v}"}
                for ck, cmap in (ax.get("companions") or {}).items():  # coupled fields (e.g. PBL -> surface layer)
                    if v in cmap:
                        spec["design"][ck] = {"value": cmap[v],
                                              "reason": f"coupled with axis '{n}' = {v}"}
        (mdir / "build").mkdir(parents=True, exist_ok=True)
        (mdir / "experiment.yaml").write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        designs[mid] = {k: spec["design"][k]["value"] for k in spec["design"]}
        entry = {"member": mid, "axis_values": vals}
        try:
            rep = emit.emit(spec, mdir / "build")
            entry["emit"] = rep["verdict"]
            entry["namelists"] = {f: v["sha256"] for f, v in rep["files"].items()}
        except emit.EmitError as e:
            entry["emit"] = f"REFUSED: {e}"
        if tbl_base:
            tbl = tbl_base
            for n, v in vals.items():
                if n in urb_axes:
                    tbl = _set_urbparm(tbl, urb_axes[n]["target"].split(":", 1)[1], float(v))
            p = mdir / "build" / "URBPARM.TBL"
            p.write_text(tbl, encoding="utf-8")
            entry["urbparm_sha256"] = _sha(p)
            tables[mid] = _urbparm_keys(tbl)
        (mdir / "manifest.yaml").write_text(
            yaml.safe_dump(entry, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        manifest["members"].append(entry)

    # ---- constructional guarantees (checked, not promised)
    nl_targets = {a["target"] for a in nl_axes.values()} | {
        ck for a in nl_axes.values() for ck in (a.get("companions") or {})}
    mids = list(designs)
    for a, b_ in itertools.combinations(mids, 2):
        diff = {k for k in set(designs[a]) | set(designs[b_]) if designs[a].get(k) != designs[b_].get(k)}
        assert diff <= nl_targets, f"members {a}/{b_} differ outside declared axes: {sorted(diff - nl_targets)}"
    if tables:
        base_keys = _urbparm_keys(tbl_base)
        declared = {a["target"].split(":", 1)[1] for a in urb_axes.values()}
        for m, tbl in tables.items():
            changed = {k for k in tbl if tbl[k] != base_keys[k]}
            assert changed <= declared, f"member {m}: URBPARM changed undeclared keys {sorted(changed - declared)}"

    manifest["guarantee"] = ("members differ only on declared namelist axes; "
                             "URBPARM differs only on declared keys (both verified over all pairs)")
    (case / "batch_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    manifest["ok"] = True
    manifest["members_dir"] = str(members_dir)
    return manifest
