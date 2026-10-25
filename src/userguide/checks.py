"""checks — compile stage: derive fields from the approved design and check every compile rule.

Rule ids are shared with field_map.yaml and dictionary.yaml. Each check returns
PASS / FAIL / WARN / DEFERRED (needs data that only exists at launch, e.g. met_em levels).
Any FAIL sends the case back to Review.
"""

from __future__ import annotations

import datetime as dt
import re

def _fm() -> dict:
    import yaml as _yaml
    from pathlib import Path as _P
    return (_yaml.safe_load((_P(__file__).parent / "field_map.yaml").read_text(encoding="utf-8"))
            .get("fields") or {})


def per_domain_fields() -> set[str]:
    """Fields that may take one value per domain (declared in field_map.yaml)."""
    return {k for k, v in _fm().items()
            if v.get("per_domain_hint") or (v.get("constraint") or {}).get("kind") == "equal_or_zero"}


def _sources() -> dict:
    import yaml as _yaml
    from pathlib import Path as _P
    return _yaml.safe_load((_P(__file__).parent / "sources.yaml").read_text(encoding="utf-8")) or {}


def build_dir(label: str | None = None):
    """references/upstream/wrf-<version>/builds/<label> for the configured build (None if absent)."""
    from pathlib import Path as _P
    src = _sources()
    label = label or src.get("default_build")
    if not label:
        return None
    d = _P(__file__).parent.parents[1] / "references" / "upstream" / f"wrf-{src.get('version')}" / "builds" / label
    return d if d.is_dir() else None


def build_accepts(label: str | None = None) -> set[str] | None:
    """Namelist variables the configured compiled wrf.exe accepts (None when no build profile is present)."""
    import yaml as _yaml
    d = build_dir(label)
    if not d:
        return None
    prof = _yaml.safe_load((d / "build_profile.yaml").read_text(encoding="utf-8"))
    text = (d / prof["namelist_statements"]["file"]).read_text(encoding="utf-8", errors="replace")
    return {v.lower() for _, v in re.findall(r"NAMELIST\s*/\s*([a-z_0-9]+)\s*/\s*([a-z_0-9]+)", text, re.I)}


def build_defines(label: str | None = None) -> dict[str, str]:
    """Preprocessor switches recorded for the configured build (BUILD_RRTMG_FAST=0, BUILD_SBM_FAST=1, ...)."""
    import yaml as _yaml
    d = build_dir(label)
    if not d:
        return {}
    prof = _yaml.safe_load((d / "build_profile.yaml").read_text(encoding="utf-8"))
    out = {}
    for item in (prof.get("configure_wrf") or {}).get("defines") or []:
        k, _, v = str(item).partition("=")
        out[k] = v or "1"
    return out


def constraints() -> dict[str, dict]:
    """Per-field domain constraints declared in field_map.yaml: kind / because / source."""
    return {k: v["constraint"] for k, v in _fm().items() if v.get("constraint")}


PER_DOMAIN = per_domain_fields()
PBL_SFCLAY = {1: {1, 91}, 2: {2}, 8: {1, 91}, 10: {10}}

# Start-up conditions of wrf.exe are declared in field_map.yaml (constraint: kind/because/source), so the YAML is
# the single place a domain rule lives. Only the build-dependent options remain here (they depend on how wrf.exe was
# compiled, not on a namelist value): see CAM_BUILD_DEPENDENT.
CAM = "share/module_check_a_mundo.F"
# accepted or refused depending on how wrf.exe was compiled: (field, value) -> (line, why, required define)
CAM_BUILD_DEPENDENT = {
    ("input:ra_lw_physics", 5): (683, "Goddard radiation with real*8 floats", None),
    ("input:ra_sw_physics", 5): (683, "Goddard radiation with real*8 floats", None),
    ("input:cu_physics", 3): (946, "GF cumulus needs a gamma intrinsic", None),
    ("input:ra_lw_physics", 24): (2872, "RRTMG fast radiation", "BUILD_RRTMG_FAST"),
    ("input:ra_sw_physics", 24): (2872, "RRTMG fast radiation", "BUILD_RRTMG_FAST"),
    ("input:ra_lw_physics", 14): (2888, "RRTMK radiation", "BUILD_RRTMK"),
    ("input:ra_sw_physics", 14): (2888, "RRTMK radiation", "BUILD_RRTMK"),
    ("input:mp_physics", 30): (2855, "fast spectral bin microphysics", "BUILD_SBM_FAST"),
}
URBAN_PBL = {1, 2, 8}
URBAN_LSM = {2, 4}      # UG: Noah; README.namelist 4.6.1: "Noah and Noah-MP LSMs"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}:\d{2}:\d{2}$")


def _num(x):
    s = str(x).strip()
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def _list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [_num(x) for x in v]
    return [_num(x) for x in str(v).split(",") if str(x).strip()]


def normalise(design: dict) -> dict:
    """{id: value | {value, reason}} -> {id: list}"""
    out = {}
    for k, v in (design or {}).items():
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        out[k] = _list(v)
    return out


def run(design: dict, design_ids: list[str]) -> dict:
    d = normalise(design)
    res: list[dict] = []

    def add(rid, status, fields, detail):
        res.append({"rule": rid, "status": status, "fields": fields, "detail": detail})

    # ---- completeness and shape (a field with show_if is required only when its condition holds)
    show_if = {}
    try:
        import yaml as _yaml
        from pathlib import Path as _P
        _fm = _yaml.safe_load((_P(__file__).parent / "field_map.yaml").read_text(encoding="utf-8"))
        show_if = {k: v.get("show_if") for k, v in _fm["fields"].items() if v.get("show_if")}
    except Exception:
        pass

    def applicable(k) -> bool:
        cond = show_if.get(k)
        if not cond:
            return True
        for ref, allowed in cond.items():
            cur = [str(x).strip() for x in d.get(ref, [])]
            if not any(str(a) in [str(x) for x in allowed] for a in cur):
                return False
        return True

    missing = [k for k in design_ids if not d.get(k) and applicable(k)]
    for k in missing:
        add("required", "FAIL", [k], "no value")
    n = int(d["wps:max_dom"][0]) if d.get("wps:max_dom") and isinstance(d["wps:max_dom"][0], int) else None
    if n:
        for k in PER_DOMAIN & set(d):
            if d[k] and len(d[k]) not in (1, n):
                add("per_domain_length", "FAIL", [k], f"{len(d[k])} values for max_dom={n}")
            elif d[k] and len(d[k]) == 1 and n > 1:
                d[k] = d[k] * n
        for k, con in constraints().items():
            v = d.get(k) or []
            if con["kind"] == "same_all_domains" and len(set(v)) > 1:
                add("domain_constraint", "FAIL", [k],
                    f"{k.split(':')[1]} must be one value for every domain — {con['because']} "
                    f"({con['source']}); got {v}")
            elif con["kind"] == "equal_or_zero" and v and any(x not in (v[0], 0) for x in v[1:]):
                add("domain_constraint", "FAIL", [k],
                    f"{k.split(':')[1]}: inner domains must be d01 or 0 — {con['because']} "
                    f"({con['source']}); got {v}")
    for k in ("wps:start_date", "wps:end_date"):
        if d.get(k) and not DATE.match(str(d[k][0])):
            add("date_format", "FAIL", [k], "expected YYYY-MM-DD_hh:mm:ss")

    g = lambda k, i=0: d[k][i] if d.get(k) and len(d[k]) > i else None  # noqa: E731
    derived: dict = {}

    # ---- domains
    shape_ok = n and all(len(d.get(k) or [None] * n) == n for k in PER_DOMAIN if k.startswith("wps:"))
    if shape_ok and all(d.get(k) for k in ("wps:parent_grid_ratio", "wps:e_we", "wps:e_sn", "wps:dx")):
        r, we, sn = d["wps:parent_grid_ratio"], d["wps:e_we"], d["wps:e_sn"]
        ip, jp = d.get("wps:i_parent_start") or [1] * n, d.get("wps:j_parent_start") or [1] * n
        bad = [i + 1 for i in range(1, n) if r[i] not in (3, 5) and (not isinstance(r[i], int) or r[i] % 2 == 0)]
        add("ratio_odd", "FAIL" if bad else "PASS", ["wps:parent_grid_ratio"],
            f"even/invalid ratio in domain(s) {bad}" if bad else f"ratios {r}")
        bad = [i + 1 for i in range(1, n) if (we[i] - 1) % r[i] or (sn[i] - 1) % r[i]]
        add("nest_divisible", "FAIL" if bad else "PASS", ["wps:e_we", "wps:e_sn"],
            f"(e−1) not divisible by ratio in domain(s) {bad}" if bad else "all nests divisible")
        bad = [i + 1 for i in range(1, n)
               if ip[i] < 1 or jp[i] < 1 or ip[i] + (we[i] - 1) // r[i] > we[i - 1] or jp[i] + (sn[i] - 1) // r[i] > sn[i - 1]]
        add("nest_inside_parent", "FAIL" if bad else "PASS", ["wps:i_parent_start", "wps:j_parent_start"],
            f"domain(s) {bad} extend beyond the parent" if bad else "all nests inside parents")
        small = [i + 1 for i in range(n) if we[i] < 100 or sn[i] < 100]
        add("domain_min_100", "WARN" if small else "PASS", ["wps:e_we", "wps:e_sn"],
            f"domain(s) {small} smaller than 100×100" if small else "all ≥ 100×100")
        dx = [float(d["wps:dx"][0])]
        for i in range(1, n):
            dx.append(dx[i - 1] / r[i])
        derived["input:dx"] = derived["input:dy"] = [round(x, 3) for x in dx]
        derived["input:max_dom"] = [n]
        derived["input:e_we"], derived["input:e_sn"] = we, sn
        derived["input:parent_grid_ratio"] = derived["input:parent_time_step_ratio"] = r
        derived["input:i_parent_start"], derived["input:j_parent_start"] = ip, jp
        derived["input:grid_id"] = list(range(1, n + 1))
        derived["input:parent_id"] = [1] + list(range(1, n))
        derived["wps:parent_id"] = derived["input:parent_id"]
        derived["wps:dy"] = [float(d["wps:dx"][0])]
        ts = int(5 * dx[0] / 1000)
        derived["input:time_step"] = [ts]
        derived["input:radt"] = [max(1, round(x / 1000)) for x in dx]
        add("dx_chain", "PASS", ["wps:dx"], f"dx = {derived['input:dx']} m")
        add("wps_wrf_same_source", "PASS", ["wps:dx", "wps:e_we", "wps:e_sn"], "WPS and WRF fields emitted from one source")
        add("cfl_timestep", "PASS" if ts <= 6 * dx[0] / 1000 else "FAIL", ["input:time_step"], f"time_step = {ts} s (≤ 6·dx = {6 * dx[0] / 1000:g} s)")
    if d.get("input:e_vert"):
        ev = d["input:e_vert"]
        add("evert_same", "PASS" if len(set(ev)) == 1 else "FAIL", ["input:e_vert"], f"e_vert = {ev}")
        add("evert_min", "PASS" if min(ev) >= 35 else "WARN", ["input:e_vert"], f"e_vert = {min(ev)}")

    # ---- time
    try:
        t0 = dt.datetime.strptime(str(g("wps:start_date")), "%Y-%m-%d_%H:%M:%S")
        t1 = dt.datetime.strptime(str(g("wps:end_date")), "%Y-%m-%d_%H:%M:%S")
        hours = (t1 - t0).total_seconds() / 3600
        if hours <= 0:
            add("period_positive", "FAIL", ["wps:start_date", "wps:end_date"], "end is not after start")
        derived.update({"input:start_year": [t0.year], "input:start_month": [t0.month], "input:start_day": [t0.day],
                        "input:start_hour": [t0.hour], "input:end_year": [t1.year], "input:end_month": [t1.month],
                        "input:end_day": [t1.day], "input:end_hour": [t1.hour], "input:run_days": [0],
                        "input:run_hours": [int(hours)]})
    except (TypeError, ValueError):
        pass

    # ---- physics / urban
    urban = [u for u in d.get("input:sf_urban_physics", []) if u]
    lsm, pbl, sfc, lw = g("input:sf_surface_physics"), g("input:bl_pbl_physics"), g("input:sf_sfclay_physics"), g("input:ra_lw_physics")
    if urban:
        add("urban_noah", "PASS" if lsm in URBAN_LSM else "FAIL", ["input:sf_urban_physics", "input:sf_surface_physics"],
            f"sf_surface_physics = {lsm}")
        if any(u in (2, 3) for u in urban):
            add("urban_bep_pbl", "PASS" if pbl in URBAN_PBL else "FAIL", ["input:sf_urban_physics", "input:bl_pbl_physics"],
                f"bl_pbl_physics = {pbl}")
        add("urbparm_effective", "DEFERRED", ["input:sf_urban_physics"],
            "needs geo_em land use and URBPARM.TBL; checked at Launch")
    if pbl in PBL_SFCLAY:
        add("pbl_sfclay_compat", "PASS" if sfc in PBL_SFCLAY[pbl] else "FAIL", ["input:bl_pbl_physics", "input:sf_sfclay_physics"],
            f"PBL {pbl} → sf_sfclay {sorted(PBL_SFCLAY[pbl])}, got {sfc}")
    elif pbl is not None:
        add("pbl_sfclay_compat", "WARN", ["input:bl_pbl_physics"], f"no compatibility entry for PBL {pbl}")
    if sfc == 10 and pbl != 10:   # the reverse direction is refused too
        add("pbl_sfclay_compat", "FAIL", ["input:bl_pbl_physics", "input:sf_sfclay_physics"],
            f"sf_sfclay_physics=10 requires bl_pbl_physics=10 ({CAM}:2301)")
    if 32 in (d.get("input:mp_physics") or []):
        add("cam_full_sbm", "FAIL", ["input:mp_physics"],
            f"full bin spectral microphysics (32) is refused; use the fast version 30 ({CAM}:2586)")
    defs = build_defines()
    for (k, val), (line, why, define) in CAM_BUILD_DEPENDENT.items():
        if val in (d.get(k) or []):
            if define is None:      # decided by the compiler, not by a build flag
                add("cam_build_dependent", "DEFERRED", [k], f"{k.split(':')[1]}={val}: {why} ({CAM}:{line}); "
                    "verify against the compiler used for wrf.exe")
                continue
            have = defs.get(define)
            if have is None:
                add("cam_build_dependent", "DEFERRED", [k], f"{k.split(':')[1]}={val}: {why} needs {define} "
                    f"in the wrf.exe build ({CAM}:{line}); no build profile is configured")
            elif str(have) in ("0", "false", "False", ""):
                add("cam_build_dependent", "FAIL", [k], f"{k.split(':')[1]}={val}: {why} but the target wrf.exe "
                    f"was built with {define}={have} ({CAM}:{line})")
            else:
                add("cam_build_dependent", "PASS", [k], f"{k.split(':')[1]}={val}: {why}, {define}={have}")
    if lw is not None:
        derived["input:o3input"] = [2 if lw == 4 else 0]
        if lw == 4:
            add("rrtmg_o3input", "PASS", ["input:ra_lw_physics", "input:o3input"], "o3input = 2 set by the compiler")
    # model levels (e_vert) and input levels (num_metgrid_levels) are different quantities: real.exe interpolates
    # the meteorological input onto the model grid, so the two need not agree. What the model does react to is
    # coarse input: below 20 input levels it rewrites lagrange_order to 1 (check_a_mundo.F:1041).
    add("vertical_grid_distinct", "PASS", ["input:e_vert", "input:num_metgrid_levels"],
        "e_vert (model levels) and num_metgrid_levels (input levels) are different quantities; real.exe interpolates, "
        "so equality is not required")
    add("lagrange_coarse_input", "DEFERRED", ["input:num_metgrid_levels"],
        "with 20 or fewer input levels wrf.exe rewrites lagrange_order to 1 "
        f"({CAM}:1041); the count is read from met_em at Launch")
    add("land_cat_match", "DEFERRED", ["input:num_land_cat"], "read from geo_em files at Launch")

    verdict = "FAIL" if any(r["status"] == "FAIL" for r in res) else "PASS"
    return {"verdict": verdict, "rules": res, "derived": derived}
