"""The CLI is the kernel interface: discuss -> select -> approve -> flow-run -> flow-status.

The studio calls the same functions over HTTP; these tests pin the command-line path, including the guard that
keeps approval human (a typed literal, the CLI form of pressing Submit).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.userguide import __main__ as cli
from src.userguide.flow import discussion, runner, state
from tests.flow_fixtures import PASSING_DESIGN
from tests.test_discussion import MockClient, _client


def _run(argv, capsys):
    rc = cli.main(argv)
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip().startswith("{") else out)


def test_cli_discuss_then_select_then_approve_then_run(tmp_path: Path, capsys):
    """End to end without a live model: the discussion client is injected, everything else is the real CLI path."""
    case = tmp_path / "case"
    case.mkdir()
    (case / "request.md").write_text("urban albedo sensitivity for one August week", encoding="utf-8")

    record = discussion.discuss(case, client=_client())
    assert record["status"] == "complete"

    rc, out = _run(["select", str(case), "--alternative", "A"], capsys)
    assert rc == 0 and out["selection"]["alternative"] == "A"
    # the selection lets Decompose pass its gate (the stage machine moves it), but that is not approval:
    # Review still holds no valid approval until the human approves.
    assert state.Journal(case, "request").current() == "review"
    approval = yaml.safe_load((case / "approval.yaml").read_text()) if (case / "approval.yaml").exists() else {}
    assert approval.get("valid") is not True   # a selection invalidates any earlier approval

    rc, out = _run(["approve", str(case), "--confirm", "approve"], capsys)
    assert rc == 0, out
    assert out["approval"]["verdict"] == "PASS"
    assert out["approval"]["stage"] == "compile"
    assert yaml.safe_load((case / "approval.yaml").read_text())["valid"] is True
    assert (case / "experiment.yaml").exists()

    rc, out = _run(["flow-run", str(case)], capsys)
    assert rc == 0
    assert out["flow"]["status"] == "waiting_external"
    assert out["flow"]["stage"] == "monitor"
    # the deterministic half really produced a run directory and never submitted it
    assert (case / "build" / "namelist.input").exists()
    assert yaml.safe_load((case / "run" / "run_manifest.yaml").read_text())["submitted"] is False

    rc, out = _run(["flow-status", str(case)], capsys)
    assert rc == 0 and out["flow"]["current"] == "monitor"


def test_cli_approve_refuses_without_the_typed_confirmation(tmp_path: Path, capsys):
    case = tmp_path / "case"
    case.mkdir()
    (case / "request.md").write_text("x", encoding="utf-8")
    discussion.discuss(case, client=_client())
    _run(["select", str(case), "--alternative", "A"], capsys)

    with pytest.raises(ValueError, match='literal confirmation "approve"'):
        cli.main(["approve", str(case)])
    assert not (case / "approval.yaml").exists() or not yaml.safe_load(
        (case / "approval.yaml").read_text())["valid"]


def test_cli_approve_refuses_an_incomplete_discussion(tmp_path: Path, capsys):
    case = tmp_path / "case"
    case.mkdir()
    (case / "request.md").write_text("x", encoding="utf-8")
    discussion.discuss(case, client=MockClient([{"design": {}}, {"design": {}}]))
    with pytest.raises(ValueError, match="incomplete discussion|no human-selected draft"):
        cli.main(["approve", str(case), "--confirm", "approve"])


def test_cli_approve_refuses_a_changed_request(tmp_path: Path, capsys):
    """Approval is bound to the content the person saw."""
    case = tmp_path / "case"
    case.mkdir()
    (case / "request.md").write_text("original request", encoding="utf-8")
    discussion.discuss(case, client=_client())
    _run(["select", str(case), "--alternative", "A"], capsys)
    (case / "draft.yaml").write_text(
        yaml.safe_dump({"design": {k: {"value": v, "reason": "edited"} for k, v in PASSING_DESIGN.items()}}),
        encoding="utf-8")
    with pytest.raises(ValueError):
        cli.main(["approve", str(case), "--confirm", "approve"])


def test_cli_flow_graph_needs_no_case(capsys):
    rc = cli.main(["flow-graph"])
    out = capsys.readouterr().out
    assert rc == 0 and "graph LR" in out and "restart" in out
