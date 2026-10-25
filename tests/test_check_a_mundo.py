"""WRF's own start-up checks (share/module_check_a_mundo.F, WRF 4.6.1) moved in front of submission.

Each case reproduces a condition that wrf.exe refuses or silently rewrites, and asserts that the compile checks
catch it before the job is queued. Line numbers refer to the 4.6.1 source and are part of the rule message.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.userguide import checks, check_a_mundo
from tests.flow_fixtures import PASSING_DESIGN

NESTED = dict(PASSING_DESIGN, **{
    "wps:max_dom": "2", "wps:parent_grid_ratio": "1,3", "wps:e_we": "100,121", "wps:e_sn": "100,121",
    "wps:i_parent_start": "1,30", "wps:j_parent_start": "1,30", "input:cu_physics": "0,0",
    "input:sf_urban_physics": "0,0", "input:history_interval": "60,60", "input:frames_per_outfile": "24,24",
})


def _rules(design, rule=None):
    rep = checks.run(design, list(design))
    return [r for r in rep["rules"] if rule is None or r["rule"] == rule], rep["verdict"]


def test_baseline_nested_design_passes():
    rules, verdict = _rules(NESTED)
    assert verdict == "PASS", [r for r in rules if r["status"] == "FAIL"]


@pytest.mark.parametrize("field,line", [("input:cu_physics", 928)])
def test_inner_domain_must_equal_d01_or_switch_off(field, line):
    assert _rules(dict(NESTED, **{field: "1,0"}))[1] == "PASS"          # inner domain switched off: allowed
    rules, verdict = _rules(dict(NESTED, **{field: "1,16"}), "domain_constraint")
    assert verdict == "FAIL" and rules and f":{line}" in rules[0]["detail"]


def test_urban_scheme_must_be_one_value_for_every_domain():
    """The rule is declared in field_map.yaml; 0,0,3 is refused with the WRF reason, not silently rewritten."""
    con = checks.constraints()["input:sf_urban_physics"]
    assert con["kind"] == "same_all_domains" and con["source"].endswith(":1061")
    rules, verdict = _rules(dict(NESTED, **{"input:sf_urban_physics": "0,0,3"}), "domain_constraint")
    assert verdict == "FAIL"
    assert ":1061" in rules[0]["detail"] and "innermost" in rules[0]["detail"]
    assert _rules(dict(NESTED, **{"input:sf_urban_physics": "3"}))[1] == "PASS"   # one value: expanded to all domains


def test_temf_pbl_and_surface_layer_must_pair_both_ways():
    rules, verdict = _rules(dict(PASSING_DESIGN, **{"input:sf_sfclay_physics": "10"}), "pbl_sfclay_compat")
    assert verdict == "FAIL" and any(":2301" in r["detail"] for r in rules)
    _, verdict = _rules(dict(PASSING_DESIGN, **{"input:bl_pbl_physics": "10", "input:sf_sfclay_physics": "10"}))
    assert verdict == "PASS"


def test_full_bin_microphysics_is_refused():
    rules, verdict = _rules(dict(PASSING_DESIGN, **{"input:mp_physics": "32"}), "cam_full_sbm")
    assert verdict == "FAIL" and ":2586" in rules[0]["detail"]


@pytest.mark.parametrize("field,value,line", [("input:cu_physics", "3", 946), ("input:ra_sw_physics", "5", 683)])
def test_compiler_dependent_options_stay_deferred(field, value, line):
    rules, _ = _rules(dict(PASSING_DESIGN, **{field: value}), "cam_build_dependent")
    assert rules and rules[0]["status"] == "DEFERRED" and f":{line}" in rules[0]["detail"]


def test_build_dependent_options_are_decided_by_the_recorded_build():
    """A recorded build turns the option into PASS or FAIL instead of 'check it later'."""
    fails, verdict = _rules(dict(PASSING_DESIGN, **{"input:ra_lw_physics": "24"}), "cam_build_dependent")
    assert verdict == "FAIL" and "BUILD_RRTMG_FAST=0" in fails[0]["detail"]      # recorded build: RRTMG fast off
    passes, verdict = _rules(dict(PASSING_DESIGN, **{"input:mp_physics": "30"}), "cam_build_dependent")
    assert verdict == "PASS" and "BUILD_SBM_FAST=1" in passes[0]["detail"]        # recorded build: SBM fast on


SRC = Path(__file__).resolve().parents[1] / "references" / "upstream" / "wrf-4.6.1" / "check_a_mundo.json"


@pytest.mark.skipif(not SRC.exists(), reason="extracted start-up checks not present")
def test_extracted_ledger_matches_the_ported_lines():
    """Every line cited by a ported rule is an extracted refusal or rewrite in the 4.6.1 ledger."""
    led = json.loads(SRC.read_text())
    lines = {i["line"] for i in led["items"]}
    cited = {int(c["source"].rsplit(":", 1)[1]) for c in checks.constraints().values()} \
        | {v[0] for v in checks.CAM_BUILD_DEPENDENT.values()} | {2301, 2586}
    assert cited <= lines, sorted(cited - lines)


def test_model_levels_and_input_levels_are_not_required_to_agree():
    """e_vert and num_metgrid_levels are different quantities; real.exe interpolates, so no equality rule exists."""
    rules, verdict = _rules(PASSING_DESIGN)
    assert verdict == "PASS"
    ids = {r["rule"] for r in rules}
    assert "vertical_grid_distinct" in ids and "metgrid_levels_match" not in ids
    distinct = next(r for r in rules if r["rule"] == "vertical_grid_distinct")
    assert distinct["status"] == "PASS" and "interpolates" in distinct["detail"]
    coarse = next(r for r in rules if r["rule"] == "lagrange_coarse_input")
    assert coarse["status"] == "DEFERRED" and ":1041" in coarse["detail"]
