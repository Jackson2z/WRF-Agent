"""The measurement script must keep producing the numbers the paper quotes."""

from __future__ import annotations

from src.userguide import measure_fidelity as M


def test_every_injected_fault_is_detected_as_expected():
    r = M.measure_constraints()
    assert r["valid_spec_verdict"] == "PASS"
    assert r["caught"] == r["faults"], [x for x in r["rows"] if not x["caught"]]


def test_every_enforcing_rule_has_a_positive_and_a_negative_or_is_measured_unreachable():
    r = M.measure_constraints()
    assert r["inventory"]["enforcing_gaps"] == [], r["inventory"]["enforcing_gaps"]
    assert r["domain_constraints"]["untested"] == []
    assert r["cfl_timestep_sweep"]["statuses"] == ["PASS"]     # the documented unreachable negative


def test_approval_does_not_survive_a_post_approval_edit():
    r = M.measure_approval()
    assert r["invalidated"] == r["edits"], [x for x in r["rows"] if x["approval_still_valid"] and not x["control"]]
    assert r["controls_still_valid"] == r["controls"], [x for x in r["rows"] if x["control"]]


def test_generation_is_deterministic_and_fully_labelled():
    r = M.measure_generation()
    assert r["identical"] and r["provenance_complete"] and r["params_emitted"] > 50


def test_a_persons_edit_is_recorded_not_overwritten():
    r = M.measure_edit_provenance()
    assert all(r.values()), r
