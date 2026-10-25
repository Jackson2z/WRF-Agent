"""Regression tests for human approval and deterministic execution boundary."""
from pathlib import Path
import yaml
from src.userguide.flow import actions, dsl, runner, state
from src.userguide import dashboard
from tests.flow_fixtures import PASSING_DESIGN, write_yaml

FLOW = dsl.load()
FIELD_MAP = Path(dashboard.HERE / 'field_map.yaml')


def _approve(case, draft):
    (case / 'request.md').write_text('one day over Singapore\n')
    write_yaml(case / 'draft.yaml', draft)
    state.Journal(case, 'review').ensure_open()
    return dashboard.submit(case, PASSING_DESIGN, FIELD_MAP)


def test_empty_case_waits_for_user(case):
    assert runner.run(case)['status'] == 'waiting_user'
    assert state.Journal(case).current() == 'request'


def test_request_advances_to_decompose_and_waits_for_the_discussion(case):
    """Stage 2 is where the experts work: without a discussion it waits, it does not fail or skip ahead."""
    (case / 'request.md').write_text('one day')
    out = runner.run(case)
    assert out['status'] == 'waiting_external'
    assert out['stage'] == 'decompose'
    assert 'discussion' in out['detail']
    assert state.Journal(case, 'request').current() == 'decompose'


def test_draft_does_not_approve_itself(case, draft_yaml):
    (case / 'request.md').write_text('one day')
    write_yaml(case / 'draft.yaml', draft_yaml)
    assert runner.run(case)['status'] == 'waiting_user'
    assert state.Journal(case).current() == 'review'  # draft alone cannot advance through approval


def test_compile_failure_requires_fresh_submit(case, draft_yaml):
    assert _approve(case, draft_yaml)['verdict'] == 'PASS'
    spec = yaml.safe_load((case / 'experiment.yaml').read_text())
    spec['design']['input:sf_sfclay_physics'] = '1'
    write_yaml(case / 'experiment.yaml', spec)
    out = runner.run(case)
    assert out['status'] == 'waiting_user' and out['stage'] == 'review'
    assert runner.run(case)['status'] == 'waiting_user'
    assert actions.gate_spec_approved(case)['ok'] is False


def test_approved_chain_prepares_then_waits_without_model(case, draft_yaml):
    assert _approve(case, draft_yaml)['verdict'] == 'PASS'
    out = runner.run(case)
    assert out['status'] == 'waiting_external', out
    assert out['stage'] == 'monitor'
    assert (case / 'build' / 'namelist.input').exists()
    assert yaml.safe_load((case / 'run' / 'run_manifest.yaml').read_text())['submitted'] is False
    rev = state.Journal(case).revision
    assert runner.run(case)['stage'] == 'monitor'
    assert state.Journal(case).revision == rev


def test_dry_run_writes_nothing(case):
    assert runner.run(case, dry_run=True)['status'] == 'dry_run'
    assert not (case / 'flow.jsonl').exists()


def test_live_claim_blocks_even_same_actor(case):
    state.Journal(case, 'request').claim('agent')
    import pytest
    with pytest.raises(state.Conflict):
        runner.run(case, actor='agent')


def test_conditional_fields(case):
    design = {k: {'value': v, 'reason': 'r'} for k, v in PASSING_DESIGN.items() if not k.startswith('wps:truelat')}
    design['wps:map_proj'] = {'value': 'mercator', 'reason': 'near-equatorial'}
    write_yaml(case / 'draft.yaml', {'design': design})
    assert actions.gate_draft_ready(case)['ok']
    design['wps:map_proj']['value'] = 'lambert'
    write_yaml(case / 'draft.yaml', {'design': design})
    assert 'truelat' in actions.gate_draft_ready(case)['detail']


def test_draft_tool_refuses_a_per_domain_urban_scheme(case):
    """A draft cannot carry 0,0,3: the YAML constraint is enforced at the tool that writes the draft."""
    from src.userguide import agent_tools
    out = agent_tools.set_draft(case, {"input:sf_urban_physics": {"value": "0,0,3", "reason": "urban only inland"}})
    assert out["accepted"] is False
    assert "one value for every domain" in out["error"] and "module_check_a_mundo.F:1061" in out["error"]
    assert out["suggestion"] == "3"
    assert not (case / "draft.yaml").exists()          # nothing was written
    ok = agent_tools.set_draft(case, {"input:sf_urban_physics": {"value": "3", "reason": "BEP everywhere"}})
    assert ok["accepted"] is True


def test_draft_tool_accepts_per_domain_values_sent_as_a_list(case):
    """A list [1, 0, 0] means the same as "1,0,0": it must be judged by its values, not by its printed form."""
    from src.userguide import agent_tools
    assert agent_tools.set_draft(case, {"input:cu_physics": {"value": [1, 0, 0], "reason": "outer domain only"}})["accepted"]
    assert agent_tools.set_draft(case, {"input:sf_urban_physics": {"value": [3, 3, 3], "reason": "BEP everywhere"}})["accepted"]
    stored = yaml.safe_load((case / "draft.yaml").read_text())["design"]
    assert stored["input:cu_physics"]["value"] == "1,0,0"        # stored as the namelist text
    out = agent_tools.set_draft(case, {"input:sf_urban_physics": {"value": [0, 0, 3], "reason": "urban only inland"}})
    assert out["accepted"] is False and "one value for every domain" in out["error"]
    out = agent_tools.set_draft(case, {"input:cu_physics": {"value": [1, 2, 0], "reason": "x"}})
    assert out["accepted"] is False and "inner domains repeat d01 or use 0" in out["error"]
