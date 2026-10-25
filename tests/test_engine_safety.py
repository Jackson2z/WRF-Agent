"""Regression contracts for local deterministic execution (no simulations)."""
import threading
from pathlib import Path
import pytest
import yaml
from src.userguide.flow import actions, runner, state, dsl
from src.userguide import dashboard, runops


def put(case, name, value):
    (case / name).write_text(yaml.safe_dump(value))


def test_stale_spec_is_not_approval(tmp_path):
    put(tmp_path, 'experiment.yaml', {'design': {'old': 1}})
    assert actions.gate_spec_approved(tmp_path).get('wait')


def test_same_actor_concurrency(tmp_path, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    def gate(*args):
        entered.set()
        assert finish.wait(5)
        return 'wait', 'pending'
    monkeypatch.setattr(runner.gates, 'evaluate', gate)
    j = state.Journal(tmp_path, 'decompose')
    j.ensure_open()
    put(tmp_path, 'draft.yaml', {'design': {'dummy': {'value': '1', 'reason': 'test'}}})
    worker = threading.Thread(target=runner.run, args=(tmp_path,))
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(state.Conflict):
            runner.run(tmp_path)
    finally:
        finish.set()
        worker.join(5)


def test_foreign_release_and_same_actor_claim(tmp_path):
    j = state.Journal(tmp_path, 'request')
    c = j.claim('agent')
    with pytest.raises(state.Conflict):
        j.claim('agent')
    with pytest.raises(state.Conflict):
        j.release('agent', token='stale')
    assert j.claim_state() == c
    j.release('agent', token=c['token'])


def test_wait_does_not_charge(tmp_path, monkeypatch):
    j = state.Journal(tmp_path, 'decompose')
    j.ensure_open()
    put(tmp_path, 'draft.yaml', {'design': {'dummy': {'value': '1', 'reason': 'test'}}})
    monkeypatch.setattr(runner.gates, 'evaluate', lambda *args: ('wait', 'pending'))
    for _ in range(4):
        assert runner.run(tmp_path)['status'] == 'waiting_external'
    assert j.attempts('decompose') == 0


def test_exception_persists_bounded_failure(tmp_path, monkeypatch):
    j = state.Journal(tmp_path, 'decompose')
    j.ensure_open()
    put(tmp_path, 'draft.yaml', {'design': {'dummy': {'value': '1', 'reason': 'test'}}})
    def broken(*args):
        raise ValueError('bad gate')
    monkeypatch.setattr(runner.gates, 'evaluate', broken)
    assert runner.run(tmp_path)['status'] == 'blocked'
    assert j.attempts('decompose') == 3
    assert runner.run(tmp_path)['status'] == 'blocked'
    assert j.attempts('decompose') == 3
    assert 'ValueError' in j.last_gate('decompose')['detail']


def test_restart_failure_terminal(tmp_path):
    j = state.Journal(tmp_path, 'restart')
    j.ensure_open()
    put(tmp_path, 'experiment.yaml', {'design': {}})
    put(tmp_path, 'approval.yaml', {'valid': True, 'inputs': actions.approval_inputs(tmp_path)})
    out = runner.run(tmp_path)
    assert out['status'] == 'failed'
    assert out['stage'] == 'failed'


def test_restart_cannot_overwrite_initial_config(tmp_path, monkeypatch):
    put(tmp_path, 'restart_record.yaml', {'ok': True})
    put(tmp_path, 'experiment.yaml', {'design': {}})
    monkeypatch.setattr(actions.emit, 'emit', lambda *a, **k: pytest.fail('must not emit initial spec'))
    assert actions.prepare_launch(tmp_path).get('wait')


def test_verify_without_criteria_fails(tmp_path):
    (tmp_path / 'wrfout_d01_2025-01-01_00:00:00').touch()
    assert runops.verify(tmp_path)['verdict'] != 'PASS'


def test_dashboard_uses_journal(tmp_path):
    put(tmp_path, 'state.yaml', {'current': 'done', 'status': {}, 'history': []})
    j = state.Journal(tmp_path, 'review')
    j.ensure_open()
    assert dashboard.load_state(tmp_path)['current'] == 'review'


def test_dashboard_renders_a_terminal_failure(tmp_path):
    """done/failed are journal stages without their own page: the dashboard must not crash."""
    from src.userguide import dashboard
    from src.userguide.flow import dsl, runner, state

    (tmp_path / "request.md").write_text("x")
    flow = dsl.load()
    j = state.Journal(tmp_path, "request")
    j.ensure_open("request")
    j.transition("failed", "automation", outcome="fail", note="stalled run")
    assert runner.status(tmp_path, flow)["stage"]["id"] == "failed"
    st = dashboard.load_state(tmp_path)
    assert st["current"] == "failed"
    out = dashboard.build(tmp_path)
    assert out.name == "index.html"
    assert len(list((tmp_path / "dashboard").glob("0*.html"))) == 8
