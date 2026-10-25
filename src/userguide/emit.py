"""emit — the compile stage's product: deterministic namelist files from the approved specification.

    experiment.yaml (design) + checks.run (derived) + template defaults (wrfkit namelist_templates.yaml)
        -> case/build/namelist.wps, case/build/namelist.input, case/build/emit_manifest.yaml

Principles (paper §Methods):
* deterministic: same specification -> byte-identical namelists (no timestamps inside the files);
* no silent ignoring: every emitted key's value source is recorded (design / derived / template-default /
  deferred-default); required keys with no value are NOT guessed — they stay as explicit
  `! MISSING: <key>` comment lines and the manifest verdict becomes INCOMPLETE;
* refused on FAIL: emit() raises unless the compile check passed (force=True overrides, for experiments).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import yaml

from . import checks

from . import __version__ as ENGINE_VERSION

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REGISTRY = REPO / "build" / "userguide" / "registry_fields.yaml"
TEMPLATES = REPO / "src" / "wrfkit" / "data" / "namelist_templates.yaml"

FILE_OF = {"wps": "namelist.wps", "input": "namelist.input"}
# best-known defaults for fields the compiler must fill but can only verify at Launch (met_em/geo_em)
DEFERRED_DEFAULTS = {
    "input:num_metgrid_levels": 38,       # ERA5 model levels — verify against met_em at Launch
    "input:num_metgrid_soil_levels": 4,
    "input:num_land_cat": 21,             # MODIS — verify against geo_em at Launch
}
# projection parameters required per map_proj; anything else in this family is skipped, not demanded
PROJ_REQUIRED = {"lambert": {"wps:truelat1", "wps:truelat2", "wps:stand_lon"},
                 "polar": {"wps:truelat1", "wps:stand_lon"},
                 "mercator": {"wps:stand_lon"},
                 "lat-lon": {"wps:stand_lon"}}
PROJ_FAMILY = {"wps:truelat1", "wps:truelat2", "wps:stand_lon", "wps:pole_lat", "wps:pole_lon"}


class EmitError(RuntimeError):
    pass


def _y(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def _values(spec: dict) -> tuple[dict, dict, int]:
    """design+derived -> {field_id: [values per domain]}; returns (vals, failed_rules, n_dom)."""
    fm = _y(HERE / "field_map.yaml")
    design_ids = [k for k, v in fm["fields"].items() if v.get("class") == "design"]
    design = {k: v.get("value") if isinstance(v, dict) else v for k, v in (spec.get("design") or {}).items()}
    rep = checks.run(design, design_ids)
    failed = [r for r in rep["rules"] if r["status"] == "FAIL"]
    d = checks.normalise(design)
    for k, v in (rep.get("derived") or {}).items():
        d[k] = [v] if not isinstance(v, list) else v
    forcing = str((spec.get('forcing') or 'era5')).lower()
    if 'wps:interval_seconds' not in d:
        d['wps:interval_seconds'] = d['input:interval_seconds'] = [3600 if forcing == 'era5' else 10800]
    n = int(d["wps:max_dom"][0]) if d.get("wps:max_dom") else 1
    # one value must still be written once per domain: per-domain fields, fields wrf.exe requires to be identical
    # on every domain (declared in field_map.yaml), and namelist plumbing that is per domain by construction
    per_domain = (checks.per_domain_fields()
                  | {k for k, c in checks.constraints().items() if c["kind"] == "same_all_domains"}
                  | {"wps:parent_id", "wps:geog_data_res", "input:dy", "input:grid_id", "input:parent_id",
                     "input:parent_time_step_ratio", "input:radt", "input:input_from_file"})
    # a real-data run: only d01 takes specified lateral boundaries; every nest is a nest (WRF test/em_real defaults)
    d.setdefault("input:specified", [True] + [False] * (n - 1))
    d.setdefault("input:nested", [False] + [True] * (n - 1))
    reg_per = {k for k, m in _registry_meta().items() if m.get("per_domain")}
    for k in list(d):
        if (k in per_domain or k in reg_per or k == "input:e_vert") and len(d[k]) == 1:
            d[k] = d[k] * n
    return d, failed, n


def _registry_meta() -> dict:
    reg = _y(REGISTRY) or {}
    return {f"wps:{r['name']}" if r["file"] == "namelist.wps" else f"input:{r['name']}":
            {"block": r["block"], "default": r.get("default"), "per_domain": r.get("per_domain")}
            for r in reg.get("fields", [])}


def _fmt(v) -> str:
    if isinstance(v, bool):
        return ".true." if v else ".false."
    s = str(v)
    if s.lower() in (".true.", ".false."):
        return s.lower()
    if s.replace(".", "", 1).replace("-", "", 1).isdigit():
        return s
    return f"'{s}'"


def _emit_file(tk: str, tmpl: dict, vals: dict, sources: dict, diags: dict, proj_req: set,
               n: int = 1) -> tuple[str, list]:
    """Render one namelist file from the template section order.

    A Fortran namelist read leaves the entries it is not given at the Registry default, so a per-domain variable
    written once applies to d01 only; every per-domain value is therefore written once per domain."""
    out: list[str] = [f"! Generated by WRF-Agent compile (spec -> {FILE_OF[tk]}). Do not edit by hand:",
                      "! change the specification and recompile."]
    meta = _registry_meta()
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    missing: list[str] = []
    for section, keys in tmpl.items():
        lines = []
        for key, tval in keys.items():
            fid = f"{tk}:{key}"
            if isinstance(tval, str) and tval.startswith("{") and tval.endswith("}"):
                v = vals.get(fid)
                if v is None:
                    if fid in DEFERRED_DEFAULTS:
                        v = [DEFERRED_DEFAULTS[fid]]
                        sources[fid] = "deferred-default"
                        diags.setdefault("deferred", []).append(fid)
                    else:
                        if fid in PROJ_FAMILY and fid not in proj_req:
                            diags.setdefault("skipped_not_applicable", []).append(fid)
                            continue
                        dv = None if fid in proj_req else (meta.get(fid) or {}).get("default")
                        if dv not in (None, "", "-"):
                            v = [dv]
                            sources[fid] = "registry-default"
                        else:
                            missing.append(fid)
                            sections.setdefault(section, []).append(f" ! MISSING: {key}   (no value in the specification)")
                            if section not in order:
                                order.append(section)
                            continue
                sources.setdefault(fid, "spec/derived")
            elif fid in vals:
                v = vals[fid]
                sources.setdefault(fid, "spec/derived")
            elif fid in DEFERRED_DEFAULTS:
                # the template literal (often stale, e.g. 32 levels) must not silently win here
                v = [DEFERRED_DEFAULTS[fid]]
                sources[fid] = "deferred-default"
                diags.setdefault("deferred", []).append(fid)
            elif tval is not None and not (isinstance(tval, str) and tval.startswith("{")):
                v = [tval]
                sources[fid] = "template-default"
            else:
                continue
            vv = v if isinstance(v, list) else [v]
            if tk == "input" and len(vv) == 1 and n > 1 and (meta.get(fid) or {}).get("per_domain"):
                vv = vv * n
            sections.setdefault(section, []).append(f" {key} = {', '.join(_fmt(x) for x in vv)},")
            if section not in order:
                order.append(section)
    # residual spec/derived values that the template does not mention are still emitted, in their Registry block
    for fid in sorted(vals):
        if fid in sources or not fid.startswith(tk + ":"):
            continue
        m = meta.get(fid)
        if not m:
            continue
        vv = vals[fid] if isinstance(vals[fid], list) else [vals[fid]]
        sections.setdefault(m["block"], []).append(f" {fid.split(':', 1)[1]} = {', '.join(_fmt(x) for x in vv)},")
        if m["block"] not in order:
            order.append(m["block"])
        sources[fid] = "spec/derived(residual)"
    out += [f"&{sec}" for sec in order[:0]]
    for sec in order:
        out.append(f"&{sec}")
        out.extend(sections[sec])
        out.append("/")
    return "\n".join(out) + "\n", missing


def emit(spec: dict, out_dir: Path, force: bool = False) -> dict:
    vals, failed, n_dom = _values(spec)
    hard = [r for r in failed if r["rule"] != "required"]
    required_missing = [f for r in failed if r["rule"] == "required" for f in r["fields"]]
    if hard and not force:
        raise EmitError(f"compile check failed ({len(failed)} rule(s)); fix the specification first: "
                        + "; ".join(f"{r['rule']}: {r['detail']}" for r in hard))
    templates = _y(TEMPLATES)
    sources: dict = {}
    diags: dict = {}
    files, all_missing = {}, list(required_missing)
    proj = str(vals.get("wps:map_proj", ["lambert"])[0]).lower()
    proj_req = PROJ_REQUIRED.get(proj, set(PROJ_FAMILY))
    for tk, fname in (("wps", "namelist.wps"), ("input", "namelist.input")):
        text, missing = _emit_file(tk, templates["namelist_wps" if tk == "wps" else "namelist_input"]["sections"],
                                   vals, sources, diags, proj_req, n_dom)
        files[fname] = text
        all_missing += missing
    # specification keys the emitter does not know how to place are never dropped silently
    unplaced = sorted(k for k in (spec.get("design") or {}) if k not in sources)
    accepts = checks.build_accepts()
    # acceptance is per executable: this profile describes wrf.exe, whose namelist is namelist.input (WPS has its own)
    unsupported = sorted({k.split(":", 1)[1] for k in vals if k.startswith("input:")}
                         - (accepts or set()) - {"interval_seconds"}) if accepts else []
    if unsupported:   # wrf.exe stops while reading the namelist ("variable not found in namelist group")
        all_missing += [f"! MISSING: {u} (not accepted by the configured wrf.exe build)" for u in unsupported]
    # WRF's own start-up checks, evaluated on the namelist just generated (before anything runs)
    from . import startup_checks
    sc = startup_checks.evaluate(startup_checks.parse_namelist(files["namelist.input"]))
    startup = {"release": sc["release"], "statements": sc["statements"], "compiled_out": sc["compiled_out"],
               "decided": sc["decided"], "undecidable": sc["undecidable"],
               "refusals": [{"line": r["line"], "message": r["message"]} for r in sc["refusals"]],
               "rewrites_of_written_values": [{"line": r["line"], "target": r["target"], "domains": r["domains"],
                                              "printed_at_default": r["printed_at_default"]}
                                             for r in sc["rewrites_of_written_values"]]}
    verdict = ("REFUSED_BY_MODEL" if startup["refusals"] else
               "COMPLETE" if not all_missing and not unplaced else "INCOMPLETE")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "wrf_version": spec.get("wrf_version"),
        "engine_version": ENGINE_VERSION,
        "spec_sha256": hashlib.sha256(yaml.safe_dump(spec, sort_keys=True).encode()).hexdigest(),
        "files": {}, "value_sources": sources,
        "missing_required": all_missing, "unsupported_in_build": unsupported, "unplaced_spec_keys": unplaced,
        "deferred_defaults": diags.get("deferred", []),
        "startup_checks": startup,
        "note": "deterministic: identical specification -> identical namelist bytes; edit the spec, not the files",
    }
    for fname, text in files.items():
        (out_dir / fname).write_text(text, encoding="utf-8")
        manifest["files"][fname] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text)}
    (out_dir / "emit_manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return manifest
