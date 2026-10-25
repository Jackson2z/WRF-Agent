"""Independent proposals, preserved dissent and human-only selection/approval."""
from types import SimpleNamespace
import pytest
from src.userguide.flow import discussion, runner, state, actions
from src.userguide import dashboard
from tests.flow_fixtures import PASSING_DESIGN


class MockClient:
    def __init__(self, answers):
        self.answers = answers
        self.messages = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        import json
        self.messages.append(kw['messages'])
        if not self.answers:
            pytest.fail('unexpected model call after discussion')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.answers.pop(0))))])


def _client():
    d = {k: {'value': v, 'reason': 'independent rationale'} for k, v in PASSING_DESIGN.items()}
    proposal = {'design': d, 'assumptions': ['ERA5 available'], 'risks': ['cost'], 'open_questions': ['PBL?']}
    critique = {'agreements': [], 'disagreements': ['PBL scheme remains contested'], 'open_questions': ['Which PBL?']}
    synthesis = {'alternatives': [{'id': 'A', 'design': d, 'tradeoffs': ['high compute cost']}],
                 'disagreements': ['PBL scheme remains contested'], 'open_questions': ['Which PBL?']}
    return MockClient([proposal.copy() for _ in range(3)] + [critique.copy() for _ in range(3)] + [synthesis])


def test_discussion_selection_approval_then_deterministic_run(tmp_path):
    (tmp_path / 'request.md').write_text('simulate an urban scenario')
    client = _client()
    result = discussion.discuss(tmp_path, client=client)
    assert result['status'] == 'complete'
    assert len(client.messages) == 7
    assert all('proposals' not in client.messages[i][1]['content'] for i in range(3))
    assert result['synthesis']['disagreements'] == ['PBL scheme remains contested']
    # decompose waits: the discussion is complete but nobody has chosen an alternative yet
    out = runner.run(tmp_path)
    assert out['status'] == 'waiting_external' and out['stage'] == 'decompose', out
    discussion.select(tmp_path, 'A')
    # the selection is what lets Decompose leave for Review; the reviewer still has to approve
    assert runner.run(tmp_path)['status'] == 'waiting_user'
    assert actions.gate_spec_approved(tmp_path)['wait']
    assert dashboard.submit(tmp_path, PASSING_DESIGN, dashboard.HERE / 'field_map.yaml')['verdict'] == 'PASS'
    out = runner.run(tmp_path)
    assert out['status'] == 'waiting_external' and out['stage'] == 'monitor', out
    assert not client.answers and len(client.messages) == 7
    assert (tmp_path / 'run' / 'run_manifest.yaml').exists()
    assert state.Journal(tmp_path).current() == 'monitor'


def test_incomplete_discussion_is_not_selectable(tmp_path):
    (tmp_path / 'request.md').write_text('simulate an urban scenario')
    client = MockClient([{'design': {}}, {'design': {}}])  # invalid, then invalid again after the repair round
    assert discussion.discuss(tmp_path, client=client)['status'] == 'incomplete'
    with pytest.raises(discussion.DiscussionError):
        discussion.select(tmp_path, 'A')


def test_one_repair_round_recovers_a_dropped_field(tmp_path):
    """Real models drop fields; one bounded repair keeps the discussion usable and records the raw exchange."""
    (tmp_path / 'request.md').write_text('simulate an urban scenario')
    good = _client()
    good_proposal = good.answers[0]
    broken = {'design': {'wps:start_date': {'value': '', 'reason': ''}}, 'assumptions': [], 'risks': [],
              'open_questions': []}
    client = MockClient([broken] + good.answers)          # first answer invalid -> repaired on the second call
    result = discussion.discuss(tmp_path, client=client)
    assert result['status'] == 'complete', result.get('error')
    assert len(result['proposals']) == 3
    assert len(client.messages) == 8                      # seven planned calls plus the one repair


def test_request_change_invalidates_selection(tmp_path):
    (tmp_path / 'request.md').write_text('original')
    discussion.discuss(tmp_path, client=_client())
    (tmp_path / 'request.md').write_text('changed')
    with pytest.raises(discussion.DiscussionError):
        discussion.select(tmp_path, 'A')
