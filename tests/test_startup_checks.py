"""WRF's start-up checks evaluated on the generated namelist (src/userguide/startup_checks.py)."""

from pathlib import Path

import pytest

from src.userguide import startup_checks as SC
from src.userguide import measure_fidelity as MF

LEDGER = Path(__file__).resolve().parents[1] / "references" / "upstream" / "wrf-4.6.1" / "check_a_mundo.json"
pytestmark = pytest.mark.skipif(not (LEDGER.exists() and SC.REGISTRY_FIELDS.exists()),
                                reason="ledger or registry (python -m src.userguide parse --build ...) missing")


def test_the_generated_namelist_passes_wrfs_own_checks():
    r = SC.check_design(MF.VALID)
    assert r["refusals"] == [] and r["rewrites_of_written_values"] == []


def test_per_domain_values_are_written_for_every_domain():
    nl = SC.emitted_namelist(MF.VALID)
    reg = SC.registry_defaults()
    once = [k for k, v in nl.items() if reg.get(k, (None, False))[1] and len(v) == 1]
    assert once == []
    assert nl["specified"] == [True, False, False] and nl["nested"] == [False, True, True]


def test_known_rewrite_and_refusal_are_found():
    r = SC.check_design(dict(MF.VALID, **{"input:sf_urban_physics": "0,0,3"}))
    hit = [x for x in r["rewrites_of_written_values"] if x["target"] == "sf_urban_physics"]
    assert hit and [d["domain"] for d in hit[0]["domains"]] == [1, 2] and not hit[0]["printed_at_default"]
    r = SC.check_design(dict(MF.VALID, **{"input:mp_physics": "32"}))
    assert [x["line"] for x in r["refusals"]] == [2586]


def test_preprocessor_branches_follow_the_build():
    assert SC.cpp_true(["((EM_CORE == 1))"], SC.macros())
    assert not SC.cpp_true(["(_MULTI_BDY_FILES_)"], SC.macros())
    assert not SC.cpp_true(["(( BUILD_RRTMG_FAST != 1))"], {**SC.macros(), "BUILD_RRTMG_FAST": 1})


def test_runtime_decomposition_is_undecidable_not_refused():
    # the MPI decomposition refusal (nproc_x/nproc_y = -1, decided by the rank count) must not fire on a fixture
    r = SC.check_design(MF.VALID)
    lines = {u["line"] for u in r["undecided"]}
    assert 2989 in lines and 2989 not in {x["line"] for x in r["refusals"]}


def test_compile_gate_runs_wrfs_start_up_checks():
    from src.userguide import checks
    r = checks.run(MF.VALID, list(MF.VALID))
    assert r["verdict"] == "PASS"
    assert [x["status"] for x in r["rules"] if x["rule"] == "wrf_startup_checks"] == ["PASS"]


def test_compile_gate_fails_when_wrf_would_rewrite_a_study_defining_value(monkeypatch):
    from src.userguide import checks
    fake = {"release": "4.6.1", "statements": 1, "decided": 1, "undecidable": 0, "refusals": [],
            "rewrites_of_written_values": [{"line": 1061, "target": "sf_urban_physics", "printed_at_default": False,
                                            "replaces_study_defining": True,
                                            "domains": [{"domain": 1, "from": 0, "to": 3}]}]}
    monkeypatch.setattr(SC, "check_design", lambda design: fake)
    r = checks.run(MF.VALID, list(MF.VALID))
    hit = [x for x in r["rules"] if x["rule"] == "wrf_startup_rewrite"]
    assert r["verdict"] == "FAIL" and hit[0]["status"] == "FAIL" and ":1061" in hit[0]["detail"]


def test_emit_does_not_recurse_into_the_compile_gate(monkeypatch):
    from src.userguide import checks
    calls = []
    real = checks.run
    monkeypatch.setattr(checks, "run", lambda *a, **k: calls.append(k.get("startup", True)) or real(*a, **k))
    SC.emitted_namelist(MF.VALID)
    assert calls and not any(calls)
