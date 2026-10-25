"""flow state: journal-derived state, CAS transitions, claim leases, attempt accounting."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from src.userguide.flow.state import Conflict, FlowStateError, Journal


def test_journal_starts_at_the_given_stage(tmp_path):
    j = Journal(tmp_path, start="request")
    assert j.current() == "request"
    assert j.revision == 0
    j.ensure_open("request")
    assert j.rows()[0]["kind"] == "open"
    assert j.ensure_open("request")["seq"] == 1          # idempotent


def test_transition_increments_revision_and_moves_the_stage(tmp_path):
    j = Journal(tmp_path, start="request")
    j.ensure_open("request")
    j.transition("decompose", actor="user", note="request recorded")
    assert j.current() == "decompose"
    assert j.revision == 1
    j.transition("review", actor="agent")
    assert j.revision == 2 and j.current() == "review"


def test_compare_and_swap_rejects_a_stale_writer(tmp_path):
    j = Journal(tmp_path, start="a")
    j.ensure_open("a")
    snap = j.revision                        # writer A reads revision 0
    j.transition("b", actor="agent")
    with pytest.raises(Conflict, match="revision mismatch"):
        j.transition("c", actor="agent", expected_revision=snap)


def test_attempts_are_counted_per_visit(tmp_path):
    j = Journal(tmp_path, start="compile")
    j.ensure_open("compile")
    j.gate("compile", "fail", "rule FAIL", actor="compiler")
    j.gate("compile", "fail", "rule FAIL", actor="compiler")
    assert j.attempts("compile") == 2
    j.transition("review", actor="compiler", outcome="fail")
    j.transition("compile", actor="user")
    assert j.attempts("compile") == 0        # re-entering the stage resets its attempt count


def test_claim_blocks_a_second_live_actor(tmp_path):
    j = Journal(tmp_path, start="a")
    held = j.claim("agent", lease_seconds=600)
    j.assert_claim("agent", held["token"])   # the holder may act
    with pytest.raises(Conflict, match="claimed by agent"):
        j.claim("other", lease_seconds=600)
    with pytest.raises(Conflict, match="not other"):
        j.assert_claim("other", held["token"])


def test_same_actor_name_without_the_token_is_refused(tmp_path):
    """Two runners both called 'agent' must not be able to impersonate each other."""
    j = Journal(tmp_path, start="a")
    held = j.claim("agent", lease_seconds=600)
    with pytest.raises(Conflict):
        j.claim("agent", lease_seconds=600)          # a second run of the same actor
    with pytest.raises(Conflict):
        j.assert_claim("agent")                      # no token presented
    j.assert_claim("agent", held["token"])


def test_expired_claim_can_be_taken_over(tmp_path):
    j = Journal(tmp_path, start="a")
    held = j.claim("agent", lease_seconds=600)
    # rewrite the expiry into the past: an expired lease must not pin the case forever
    rows = j.rows()
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    rows[-1]["expires_at"] = past
    j.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    with pytest.raises(Conflict, match="expired"):
        j.assert_claim("agent", held["token"])
    taken = j.claim("other", lease_seconds=600)   # takeover succeeds once the lease is dead
    j.assert_claim("other", taken["token"])


def test_release_frees_the_case(tmp_path):
    j = Journal(tmp_path, start="a")
    held = j.claim("agent")
    j.release("agent", token=held["token"], note="runner finished")
    assert j.claim_state() is None
    j.claim("other")                          # no conflict after release


def test_release_requires_the_lease_token(tmp_path):
    """A process must not be able to free a lease it does not hold."""
    j = Journal(tmp_path, start="a")
    held = j.claim("agent")
    with pytest.raises(Conflict):
        j.release("other", token=held["token"])
    with pytest.raises(Conflict):
        j.release("agent", token="not-the-token")
    j.assert_claim("agent", held["token"])


def test_status_is_derived_from_the_journal_only(tmp_path):
    j = Journal(tmp_path, start="a")
    j.ensure_open("a")
    held = j.claim("agent")
    j.assert_claim("agent", held["token"])
    j.gate("a", "pass", "fine", actor="agent")
    j.transition("b", actor="agent")
    st = j.status()
    assert st["current"] == "b" and st["revision"] == 1
    # a fresh reader (no in-memory state) sees exactly the same thing -> resume is idempotent
    assert Journal(tmp_path, start="a").status()["current"] == "b"


def test_transition_without_a_journal_needs_a_start(tmp_path):
    with pytest.raises(FlowStateError):
        Journal(tmp_path).current()
