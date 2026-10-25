"""The deliberation study harness: arms produce the same files, metrics read them off, no judging."""

import json
from types import SimpleNamespace

import yaml

from src.userguide.flow import study
from src.userguide.flow.deliberation_metrics import metrics
from src.userguide.flow.registry import Registry

REQ = "Urban heat in Singapore, July 2024: roof and wall albedo sensitivity with BEP+BEM"


class MockClient:
    """Deterministic stand-in: answers proposals/critiques/synthesis/oneshot from design ids."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.n = 0

    def _create(self, **kw):
        self.n += 1
        system = kw["messages"][0]["content"]
        payload = json.loads(kw["messages"][1]["content"])
        ids = payload.get("design_ids") or sorted(next(
            (p["design"] for p in payload.get("proposals", {}).values()), {"design": {}}))
        if "Independent WRF" in system:
            pbl = "8" if "physics" in system.lower() else "2"
            body = {"design": {i: {"value": pbl if "pbl" in i else "2", "reason": "mock"} for i in ids},
                    "assumptions": ["a"], "risks": ["r"], "open_questions": ["q1"]}
        elif "cross-examine" in system:
            body = {"agreements": ["agree"], "disagreements": ["bl_pbl_physics: 2 vs 8"],
                    "open_questions": ["which PBL"]}
        elif "Synthesize" in system:
            a = {i: {"value": "2" if "pbl" not in i else "8", "reason": "mock"} for i in ids}
            b = {i: {"value": "2" if "pbl" not in i else "2", "reason": "mock"} for i in ids}
            body = {"alternatives": [{"id": "A", "design": a, "tradeoffs": ["BouLac"]},
                                     {"id": "B", "design": b, "tradeoffs": ["MYJ"]}],
                    "disagreements": ["bl_pbl_physics: 2 vs 8"], "open_questions": ["which PBL"]}
        else:  # oneshot
            body = {"design": {i: {"value": "2", "reason": "mock"} for i in ids}, "open_questions": ["q"]}
        msg = SimpleNamespace(content=json.dumps(body))
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


def test_all_arms_run_and_metrics_are_recorded(tmp_path):
    row = study.run_trial(REQ, arms=("multi", "single", "oneshot"), client=MockClient(),
                          root=tmp_path, trial_id="t00")
    assert set(row["arms"]) == {"multi", "single", "oneshot"}
    for arm, r in row["arms"].items():
        assert r["ok"], r
        m = r["metrics"]
        assert m["cost"]["llm_calls"] >= 1 and m["cost"]["tokens_recorded"]
        assert m["selection"]["alternative"]
    assert row["arms"]["single"]["metrics"]["specialists"] == 1
    assert row["arms"]["multi"]["metrics"]["specialists"] == len(Registry().specialists)


def test_multi_shows_divergence_and_kept_dissent(tmp_path):
    study.run_trial(REQ, arms=("multi",), client=MockClient(), root=tmp_path, trial_id="t01")
    m = metrics(tmp_path / "t01" / "multi")
    assert "input:bl_pbl_physics" in m["initial_divergence"]["list"]
    assert m["synthesis"]["kept_disagreements"] >= 1
    assert m["synthesis"]["alternatives"] == 2
    assert m["dissent"]["kept_in_synthesis"] >= 1


def test_study_record_is_resumable_jsonl(tmp_path):
    out = tmp_path / "record.jsonl"
    study.run_study([REQ, "Second request"], out, client=MockClient(), arms=("single",))
    lines = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert [l["trial"] for l in lines] == ["t00", "t01"]
    study.run_study([REQ, "Second request"], out, client=MockClient(), arms=("single",))
    assert len([l for l in out.read_text().splitlines() if l.strip()]) == 2  # nothing re-run
