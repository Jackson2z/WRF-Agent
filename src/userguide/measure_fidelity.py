#!/usr/bin/env python3
"""measure_fidelity — what the constraint set and the approval binding actually catch.

Two numbers the paper needs and cannot get from prose:

  1. constraint enforcement, by fault injection: for each constraint kind, a specification that satisfies it must be
     accepted, and a specification that violates it must be refused *with that constraint named*;
  2. approval binding, adversarially: after a specification is approved, five different kinds of later edit must
     invalidate the approval.

Plus the two properties generation claims: byte-identical re-emission, and provenance completeness.

    python3 -m src.userguide.measure_fidelity [--out build/measurements]

Everything runs on temporary case directories; no WRF job, no network, no model call.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import yaml

from . import checks, dashboard, emit
from .flow import actions

HERE = Path(__file__).resolve().parent

# A three-domain specification that satisfies every rule in the set. Values are WRF-legal and mutually consistent.
VALID = {
    "wps:start_date": "2024-07-01_00:00:00", "wps:end_date": "2024-07-09_00:00:00",
    "input:restart_interval": "1440", "wps:max_dom": "3",
    "wps:ref_lat": "1.35", "wps:ref_lon": "103.8", "wps:map_proj": "lambert",
    "wps:dx": "9000,3000,1000", "wps:parent_grid_ratio": "1,3,3",
    "wps:e_we": "100,121,121", "wps:e_sn": "100,121,121",
    "wps:i_parent_start": "1,30,30", "wps:j_parent_start": "1,30,30",
    "wps:stand_lon": "103.8", "wps:truelat1": "1.35", "wps:truelat2": "1.35",
    "input:e_vert": "45", "input:mp_physics": "6", "input:bl_pbl_physics": "2",
    "input:sf_sfclay_physics": "2", "input:sf_surface_physics": "2",
    "input:ra_lw_physics": "4", "input:ra_sw_physics": "4", "input:cu_physics": "1,0,0",
    "input:sf_urban_physics": "3", "input:use_wudapt_lcz": "0",
    "input:history_interval": "60,60,60", "input:frames_per_outfile": "24,24,24",
}

# rule id -> (label, mutation, expected status). A mutation changes exactly one field.
FAULTS = [
    ("ratio_odd", "even grid ratio", {"wps:parent_grid_ratio": "1,2,2"}, "FAIL"),
    ("nest_divisible", "inner grid not divisible", {"wps:e_we": "100,120,120"}, "FAIL"),
    ("nest_inside_parent", "nest outside parent", {"wps:i_parent_start": "1,90,90"}, "FAIL"),
    ("evert_same", "vertical levels differ", {"input:e_vert": "45,50,50"}, "FAIL"),
    ("evert_min", "too few vertical levels", {"input:e_vert": "20,20,20"}, "WARN"),
    ("domain_min_100", "domain smaller than 100 cells", {"wps:e_we": "50,50,50"}, "WARN"),
    ("pbl_sfclay_compat", "surface layer incompatible with PBL", {"input:sf_sfclay_physics": "1"}, "FAIL"),
    ("domain_constraint", "shared option differs per domain", {"input:sf_urban_physics": "0,0,3"}, "FAIL"),
    ("domain_constraint", "inner domain neither d01 nor zero", {"input:cu_physics": "1,16,0"}, "FAIL"),
    ("urban_noah", "urban scheme without an urban-capable LSM", {"input:sf_urban_physics": "2",
                                                                "input:sf_surface_physics": "1"}, "FAIL"),
    ("cam_full_sbm", "refused microphysics option", {"input:mp_physics": "32"}, "FAIL"),
    ("cam_build_dependent", "option the build does not enable", {"input:ra_lw_physics": "24"}, "FAIL"),
    ("date_format", "unparsable date", {"wps:start_date": "2024/07/01"}, "FAIL"),
    ("period_positive", "end before start", {"wps:end_date": "2024-06-30_00:00:00"}, "FAIL"),
    ("required", "a required decision removed", {"wps:end_date": ""}, "FAIL"),
    # added 2026-10-25: every enforcing rule and every declared domain constraint gets a single-field negative
    ("per_domain_length", "per-domain list of the wrong length", {"wps:e_we": "100,121"}, "FAIL"),
    ("urban_bep_pbl", "multi-layer urban scheme under a PBL outside the rule's set", {"input:bl_pbl_physics": "5"},
     "FAIL"),
    ("pbl_sfclay_compat", "PBL without a compatibility entry", {"input:bl_pbl_physics": "99"}, "WARN"),
    ("pbl_sfclay_compat", "surface layer 10 without PBL 10", {"input:sf_sfclay_physics": "10"}, "FAIL"),
    ("cam_build_dependent", "option decided by the compiler, not a build flag", {"input:cu_physics": "3,0,0"},
     "DEFERRED"),
    ("date_format", "unparsable end date", {"wps:end_date": "2024-07-09"}, "FAIL"),
    ("domain_constraint", "microphysics differs per domain", {"input:mp_physics": "6,8,8"}, "FAIL"),
    ("domain_constraint", "inner PBL neither d01 nor zero", {"input:bl_pbl_physics": "2,8,8"}, "FAIL"),
    ("domain_constraint", "surface layer differs per domain", {"input:sf_sfclay_physics": "2,1,1"}, "FAIL"),
    ("domain_constraint", "land surface differs per domain", {"input:sf_surface_physics": "2,1,1"}, "FAIL"),
    ("domain_constraint", "long-wave radiation differs per domain", {"input:ra_lw_physics": "4,1,1"}, "FAIL"),
    ("domain_constraint", "short-wave radiation differs per domain", {"input:ra_sw_physics": "4,1,1"}, "FAIL"),
]

# Positive tests for rules the valid specification does not exercise (it only enables options they never see).
POSITIVES = [
    ("cam_build_dependent", "option the recorded build enables", {"input:mp_physics": "30"}, "PASS"),
]

# Rules whose failing branch cannot be reached from a well-formed design, with the reason; measured, not asserted.
UNREACHABLE = {
    "cfl_timestep": "the time step is derived as int(5*dx/1000) s and the rule tests <= 6*dx/1000 s, so no positive dx "
                    "can fail it; it records the derivation rather than constrains the design",
}

APPROVAL_CONTROLS = [
    ("no edit", "none"),
    ("specification rewritten with identical bytes", "touch"),
    ("specification re-serialised with the same content", "reserialise"),
]

APPROVAL_EDITS = [
    ("approved value changed in the specification", "spec"),
    ("draft value changed after approval", "draft"),
    ("request rewritten after approval", "request"),
    ("discussion record replaced", "discussion"),
    ("review form edited after approval", "form"),
]


def _design(d: dict) -> dict:
    return {k: {"value": v, "reason": "measurement fixture"} for k, v in d.items()}


def run_checks(design: dict) -> dict:
    return checks.run(design, list(design))


def rule_inventory() -> dict[str, list[str]]:
    """Every rule id checks.run can emit, with the statuses written at its call sites (read from the source)."""
    import re
    text = (HERE / "checks.py").read_text(encoding="utf-8")
    inv: dict[str, set] = {}
    for rid, status_expr in re.findall(r'add\("([a-z_0-9]+)",\s*(.+?),\s*\[', text):
        inv.setdefault(rid, set()).update(re.findall(r'"(PASS|FAIL|WARN|DEFERRED)"', status_expr))
    return {k: sorted(v) for k, v in sorted(inv.items())}


def _classify(statuses: list[str]) -> str:
    s = set(statuses)
    if s <= {"PASS"}:
        return "informational"
    if s <= {"DEFERRED"}:
        return "deferred"
    return "enforcing"


def _row(rule, label, change, expect, kind):
    rep = run_checks(dict(VALID, **change))
    hit = [r for r in rep["rules"] if r["rule"] == rule]
    on_field = [r for r in hit if set(r.get("fields") or []) & set(change)]
    hit = on_field or hit                      # a domain constraint must be reported on the mutated field
    statuses = {r["status"] for r in hit}
    got = expect if expect in statuses else (hit[-1]["status"] if hit else "ABSENT")
    collateral = sorted({r["rule"] for r in rep["rules"] if r["rule"] != rule and r["status"] in ("FAIL", "WARN")})
    return {"rule": rule, "test": kind, "fault": label, "mutation": change, "expected": expect, "detected": got,
            "caught": got == expect, "case_verdict": rep["verdict"], "collateral": collateral}


def measure_constraints() -> dict:
    base = run_checks(VALID)
    assert base["verdict"] == "PASS", [r for r in base["rules"] if r["status"] == "FAIL"]
    rows = [_row(r, l, c, e, "negative") for r, l, c, e in FAULTS]
    pos_rows = [_row(r, l, c, e, "positive") for r, l, c, e in POSITIVES]
    kinds = sorted({r["rule"] for r in base["rules"]})

    inv = rule_inventory()
    base_bad = {r["rule"] for r in base["rules"] if r["status"] in ("FAIL", "WARN")}
    negative_rules = {r["rule"] for r in rows if r["caught"]}
    positive_rules = {r["rule"] for r in pos_rows if r["caught"]}
    inventory = []
    for rid, statuses in inv.items():
        cls = _classify(statuses)
        basis = ("evaluated on the valid specification" if rid in kinds else
                 "explicit positive test" if rid in positive_rules else
                 "not raised on the valid specification")
        positive = rid not in base_bad
        inventory.append({"rule": rid, "statuses": statuses, "class": cls,
                          "evaluated_on_valid_spec": rid in kinds, "positive": positive, "positive_basis": basis,
                          "negative": rid in negative_rules, "unreachable": UNREACHABLE.get(rid)})
    enforcing = [r for r in inventory if r["class"] == "enforcing"]
    gaps = [r["rule"] for r in enforcing if not (r["positive"] and (r["negative"] or r["unreachable"]))]

    # the unreachable claim is measured over a dx sweep, not asserted
    sweep = []
    for dx in (100, 333, 1000, 3000, 9000, 12345, 27000, 50000):
        rep = run_checks(dict(VALID, **{"wps:dx": f"{dx},{dx / 3:g},{dx / 9:g}"}))
        sweep += [r["status"] for r in rep["rules"] if r["rule"] == "cfl_timestep"]

    cons = {k: v["kind"] for k, v in checks.constraints().items()}
    tested_fields = {f for r in rows if r["rule"] == "domain_constraint" and r["caught"] for f in r["mutation"]}
    return {"valid_spec_verdict": base["verdict"], "rule_kinds": len(kinds), "kinds": kinds,
            "faults": len(rows), "caught": sum(r["caught"] for r in rows), "rows": rows,
            "positives": pos_rows,
            "inventory": {"rules": len(inventory),
                          "by_class": {c: sum(1 for r in inventory if r["class"] == c)
                                       for c in ("enforcing", "informational", "deferred")},
                          "enforcing_with_positive_and_negative": len(enforcing) - len(gaps),
                          "enforcing_gaps": gaps, "rows": inventory},
            "domain_constraints": {"declared": len(cons),
                                   "negative_tested": sorted(tested_fields & set(cons)),
                                   "untested": sorted(set(cons) - tested_fields)},
            "faults_without_collateral": sum(1 for r in rows if r["caught"] and not r["collateral"]),
            "cfl_timestep_sweep": {"dx_values": 8, "statuses": sorted(set(sweep))}}


def measure_approval() -> dict:
    """Approve a specification, then edit it in five different ways; the approval must not survive any of them."""
    fm = HERE / "field_map.yaml"
    tmp = Path(tempfile.mkdtemp())
    try:
        dashboard.init_case(tmp, "measurement request", None)
        (tmp / "draft.yaml").write_text(
            yaml.safe_dump({"wrf_version": "4.6.1", "design": _design(VALID)}), encoding="utf-8")
        dashboard.move(tmp, "review", "measurement fixture")
        approved = dashboard.approve(tmp, fm, by="user", confirmation="approve")
        assert approved["verdict"] == "PASS", approved
        assert actions.gate_spec_approved(tmp)["ok"], "approval should be valid right after approval"

        results = []
        for label, how in APPROVAL_EDITS + APPROVAL_CONTROLS:
            shutil.rmtree(tmp, ignore_errors=True)
            dashboard.init_case(tmp, "measurement request", None)
            (tmp / "draft.yaml").write_text(
                yaml.safe_dump({"wrf_version": "4.6.1", "design": _design(VALID)}), encoding="utf-8")
            dashboard.move(tmp, "review", "measurement fixture")
            dashboard.approve(tmp, fm, by="user", confirmation="approve")
            if how == "spec":
                spec = yaml.safe_load((tmp / "experiment.yaml").read_text())
                cur = spec["design"]["input:mp_physics"]
                spec["design"]["input:mp_physics"] = ({"value": "8", **{k: v for k, v in cur.items()
                                                                        if k != "value"}}
                                                      if isinstance(cur, dict) else "8")
                (tmp / "experiment.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
            elif how == "draft":
                (tmp / "draft.yaml").write_text(
                    yaml.safe_dump({"wrf_version": "4.6.1",
                                    "design": _design(dict(VALID, **{"input:mp_physics": "8"}))}), encoding="utf-8")
            elif how == "request":
                (tmp / "request.md").write_text("a different request\n", encoding="utf-8")
            elif how == "discussion":
                (tmp / "discussion.yaml").write_text(yaml.safe_dump({"status": "complete", "snapshot_hash": "x"}),
                                                     encoding="utf-8")
            elif how == "form":
                dashboard.save_review(tmp, dict(VALID, **{"input:mp_physics": "8"}), fm)
            elif how == "touch":
                p = tmp / "experiment.yaml"
                p.write_bytes(p.read_bytes())
            elif how == "reserialise":
                p = tmp / "experiment.yaml"
                spec = yaml.safe_load(p.read_text())
                p.write_text(yaml.safe_dump(spec, sort_keys=False, default_flow_style=False, width=200),
                             encoding="utf-8")
            gate = actions.gate_spec_approved(tmp)
            results.append({"edit": label, "kind": how, "control": how in {k for _, k in APPROVAL_CONTROLS},
                            "approval_still_valid": bool(gate["ok"]), "detail": str(gate.get("detail", ""))[:120]})
        edits = [r for r in results if not r["control"]]
        ctrls = [r for r in results if r["control"]]
        return {"edits": len(edits), "invalidated": sum(1 for r in edits if not r["approval_still_valid"]),
                "controls": len(ctrls), "controls_still_valid": sum(1 for r in ctrls if r["approval_still_valid"]),
                "rows": results}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def measure_edit_provenance() -> dict:
    """The form is pre-filled by the agent; a person's edit must be recorded as a decision, not overwrite the record.

    Checks: the agent's proposal is retained beside the person's value, the approved specification lists the changed
    decisions, and the agent's reason survives into the specification.
    """
    fm = HERE / "field_map.yaml"
    tmp = Path(tempfile.mkdtemp())
    try:
        dashboard.init_case(tmp, "measurement request", None)
        (tmp / "draft.yaml").write_text(
            yaml.safe_dump({"wrf_version": "4.6.1", "design": _design(VALID)}), encoding="utf-8")
        dashboard.move(tmp, "review", "measurement fixture")
        edited = dict(VALID, **{"input:bl_pbl_physics": "8"})      # person overrides the agent's choice of MYJ
        dashboard.approve(tmp, fm, by="user", design=edited, confirmation="approve")
        draft = yaml.safe_load((tmp / "draft.yaml").read_text())["design"]["input:bl_pbl_physics"]
        spec = yaml.safe_load((tmp / "experiment.yaml").read_text())
        return {"agent_value_retained": draft.get("agent_value") == VALID["input:bl_pbl_physics"],
                "edit_marked": draft.get("edited_by") == "user",
                "value_is_the_person_s_choice": str(draft.get("value")) == "8",
                "changed_listed_in_spec": "input:bl_pbl_physics" in (spec.get("changed_from_draft") or []),
                "agent_reason_survives": bool((spec.get("agent_reasons") or {}).get("input:bl_pbl_physics"))}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def measure_generation() -> dict:
    """Determinism, idempotency and provenance completeness of the emitted artefact."""
    a, b = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    try:
        spec = {"wrf_version": "4.6.1", "design": _design(VALID)}
        r1 = emit.emit(copy.deepcopy(spec), a)
        r2 = emit.emit(copy.deepcopy(spec), b)
        files = {}
        for name in ("namelist.wps", "namelist.input"):
            ha = hashlib.sha256((a / name).read_bytes()).hexdigest()
            hb = hashlib.sha256((b / name).read_bytes()).hexdigest()
            files[name] = {"sha256": ha, "identical": ha == hb}
        man = yaml.safe_load((a / "emit_manifest.yaml").read_text())
        sources = man.get("value_sources") or {}
        # the manifest records the value source as a short label per parameter
        unlabelled = [k for k, v in sources.items()
                      if not (str(v.get("source", "")) if isinstance(v, dict) else str(v))]
        return {"verdict": r1["verdict"], "files": files,
                "identical": all(f["identical"] for f in files.values()),
                "params_emitted": len(sources), "provenance_complete": not unlabelled,
                "unlabelled": unlabelled[:10],
                "missing_required": r1.get("missing_required") or [],
                "unsupported_in_build": r1.get("unsupported_in_build") or []}
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


STARTUP_CASES = [
    ("valid specification", {}, {"refusals": [], "rewrites_of_written_values": []}),
    ("urban scheme only on the innermost domain", {"input:sf_urban_physics": "0,0,3"},
     {"refusals": [], "rewrites_of_written_values": ["sf_urban_physics"]}),
    ("microphysics refused by the model", {"input:mp_physics": "32"},
     {"refusals": [2586], "rewrites_of_written_values": []}),
]


def measure_startup() -> dict:
    """WRF's start-up checks evaluated on the generated namelist: what is decidable, and do known cases come out right."""
    from . import startup_checks as SC
    rows = []
    for label, change, expect in STARTUP_CASES:
        r = SC.check_design(dict(VALID, **change))
        got = {"refusals": sorted(x["line"] for x in r["refusals"]),
               "rewrites_of_written_values": sorted({x["target"] for x in r["rewrites_of_written_values"]})}
        rows.append({"case": label, "mutation": change, "expected": expect, "got": got, "ok": got == expect})
    cov = SC.coverage(SC.emitted_namelist(VALID))
    return {"cases": len(rows), "ok": sum(r["ok"] for r in rows), "rows": rows, "coverage": cov}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("docs/measurements"))
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)

    constraints = measure_constraints()
    approval = measure_approval()
    generation = measure_generation()
    edits = measure_edit_provenance()
    startup = measure_startup()
    report = {"constraints": constraints, "approval": approval, "generation": generation, "edit_provenance": edits,
              "startup_checks": startup}
    (a.out / "fidelity_measurements.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    inv = constraints["inventory"]
    lines = ["# Constraint enforcement and approval binding — measured", "",
             f"- Rule inventory (read from checks.py): **{inv['rules']}** rule ids — "
             + ", ".join(f"{n} {c}" for c, n in inv["by_class"].items()),
             f"- Enforcing rules with a positive and a negative (or measured-unreachable) test: "
             f"**{inv['enforcing_with_positive_and_negative']}/{inv['by_class']['enforcing']}**"
             + (f"; gaps: {', '.join(inv['enforcing_gaps'])}" if inv["enforcing_gaps"] else ""),
             f"- Declared domain constraints negative-tested: {len(constraints['domain_constraints']['negative_tested'])}"
             f"/{constraints['domain_constraints']['declared']}",
             f"- Valid specification: verdict **{constraints['valid_spec_verdict']}**, "
             f"{constraints['rule_kinds']} rule kinds evaluated",
             f"- Fault injection: **{constraints['caught']}/{constraints['faults']}** faults detected as expected; "
             f"{constraints['faults_without_collateral']} of them raise no other rule",
             f"- cfl_timestep over {constraints['cfl_timestep_sweep']['dx_values']} dx values: "
             f"{constraints['cfl_timestep_sweep']['statuses']}", "",
             "| rule | class | statuses | positive | negative |", "|---|---|---|---|---|"]
    for r in inv["rows"]:
        neg = "yes" if r["negative"] else ("unreachable" if r["unreachable"] else "—")
        lines.append(f"| `{r['rule']}` | {r['class']} | {'/'.join(r['statuses'])} | "
                     f"{'yes' if r['positive'] else 'NO'} ({r['positive_basis']}) | {neg} |")
    lines += ["", "| rule | injected fault | expected | detected | caught | other rules raised |",
              "|---|---|---|---|---|---|"]
    for r in constraints["rows"] + constraints["positives"]:
        lines.append(f"| `{r['rule']}` | {r['fault']} | {r['expected']} | {r['detected']} | "
                     f"{'yes' if r['caught'] else 'NO'} | {', '.join(r['collateral']) or '—'} |")
    lines += ["", f"- Approval invalidated by **{approval['invalidated']}/{approval['edits']}** post-approval edits; "
                  f"still valid after **{approval['controls_still_valid']}/{approval['controls']}** non-edits", "",
              "| post-approval edit | approval still valid |", "|---|---|"]
    for r in approval["rows"]:
        lines.append(f"| {r['edit']} | {'yes' if r['approval_still_valid'] else 'no'} |")
    lines += ["", "## Edit provenance (the pre-filled form as the decision surface)", "",
              "| property | holds |", "|---|---|"]
    for k, v in edits.items():
        lines.append(f"| {k.replace('_', ' ')} | {'yes' if v else 'NO'} |")
    lines += ["", f"- Re-emission byte-identical: **{generation['identical']}** "
                  f"({', '.join(f'{k}: {v['sha256'][:12]}' for k, v in generation['files'].items())})",
              f"- Provenance complete: **{generation['provenance_complete']}** across "
              f"{generation['params_emitted']} emitted parameters"]
    cov = startup["coverage"]
    lines += ["", "## WRF start-up checks evaluated on the generated namelist", "",
              f"- Known cases reproduced: **{startup['ok']}/{startup['cases']}**",
              f"- Statements compiled out for the recorded build: {cov['compiled_out_for_build']}",
              f"- Decidable before the run: refusals {cov['refuse']['decidable']}/{cov['refuse']['in_build']}, "
              f"rewrites {cov['rewrite']['decidable']}/{cov['rewrite']['in_build']}; study-defining refusals "
              f"{cov['refuse']['involve_S_decidable']}/{cov['refuse']['involve_S']}, rewrites "
              f"{cov['rewrite']['involve_S_decidable']}/{cov['rewrite']['involve_S']}, replacements "
              f"{cov['rewrite']['replace_S_decidable']}/{cov['rewrite']['replace_S']}"]
    (a.out / "fidelity_measurements.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"constraints_caught": f"{constraints['caught']}/{constraints['faults']}",
                      "inventory": {k: v for k, v in constraints["inventory"].items() if k != "rows"},
                      "domain_constraints_untested": constraints["domain_constraints"]["untested"],
                      "approval_invalidated": f"{approval['invalidated']}/{approval['edits']}",
                      "controls_valid": f"{approval['controls_still_valid']}/{approval['controls']}",
                      "startup_cases": f"{startup['ok']}/{startup['cases']}",
                      "deterministic": generation["identical"],
                      "provenance_complete": generation["provenance_complete"],
                      "params": generation["params_emitted"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
