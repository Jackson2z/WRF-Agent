"""flow board: the five-key handoff contract."""

from __future__ import annotations

import pytest

from src.userguide.flow import board, registry

GOOD = {
    "key_findings": ["dx=3000 m with ratio 3 gives a 3 km / 1 km pair"],
    "claims_made": ["the nest covers the study region"],
    "deviations_from_outline": [],
    "open_questions": ["is the urban canopy in scope for this experiment?"],
    "downstream_flags": ["research_design: confirm the albedo perturbation is the intended intervention"],
}


def test_post_and_query_roundtrip(case):
    row = board.post(case, actor="research_design", digest=GOOD, registry=registry.Registry())
    assert row["digest"]["key_findings"] == GOOD["key_findings"]
    assert len(board.rows(case)) == 1
    assert board.open_questions(case)[0]["actor"] == "research_design"
    assert board.downstream_flags(case)[0]["flag"].startswith("research_design")
    assert board.findings(case)[0]["finding"].startswith("dx=3000")


def test_missing_key_is_rejected(case):
    bad = dict(GOOD)
    del bad["downstream_flags"]
    with pytest.raises(board.BoardError, match="missing digest keys"):
        board.post(case, actor="research_design", digest=bad)
    assert board.rows(case) == []


def test_unknown_key_is_rejected(case):
    bad = {**GOOD, "extra_thoughts": ["free prose does not belong here"]}
    with pytest.raises(board.BoardError, match="unknown digest keys"):
        board.post(case, actor="research_design", digest=bad)


def test_empty_findings_is_not_a_digest(case):
    bad = {**GOOD, "key_findings": []}
    with pytest.raises(board.BoardError, match="must not be empty"):
        board.post(case, actor="research_design", digest=bad)


def test_non_list_section_is_rejected(case):
    bad = {**GOOD, "claims_made": "one string, not a list"}
    with pytest.raises(board.BoardError, match="must be a list"):
        board.post(case, actor="research_design", digest=bad)


def test_unknown_actor_is_rejected_when_a_registry_is_given(case):
    with pytest.raises(board.BoardError, match="neither a known role"):
        board.post(case, actor="random-role", digest=GOOD, registry=registry.Registry())


def test_latest_per_actor(case):
    board.post(case, actor="research_design", digest=GOOD, registry=registry.Registry())
    second = {**GOOD, "key_findings": ["second proposal"]}
    board.post(case, actor="wrf_physics", digest=second, registry=registry.Registry())
    assert board.latest(case)["actor"] == "wrf_physics"
    assert board.latest(case, actor="research_design")["digest"]["key_findings"] == GOOD["key_findings"]
