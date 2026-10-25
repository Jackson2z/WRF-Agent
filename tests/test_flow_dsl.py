"""flow dsl + registry: graph validation, drift against the dashboard view, specialist ownership partition."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from src.userguide.flow import dsl, registry

HERE = Path(__file__).resolve().parents[1]
STAGES = HERE / "src" / "userguide" / "flow" / "stages.yaml"


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "stages.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_shipped_flow_loads_and_has_one_terminal():
    f = dsl.load()
    assert f.ids()[0] == "request"
    assert set(f.terminals()) == {"done", "failed"}
    assert f.stage("compile").owner == "compiler"
    assert f.next("verify", "pass") == "done"
    assert f.next("compile", "fail") == "review"   # back edge: a failed rule returns to the human
    assert f.stage("review").action is None         # the machine never acts at a human stage


def test_flow_matches_dashboard_view():
    """workflow.yaml (rendered) and flow/stages.yaml (executable) must not drift apart."""
    rendered = yaml.safe_load((HERE / "src" / "userguide" / "workflow.yaml").read_text(encoding="utf-8"))
    rendered_ids = [s["id"] for s in rendered["stages"]]
    executable_ids = [i for i in dsl.load().ids() if i not in ("done", "failed")]
    assert executable_ids == rendered_ids


def test_dangling_edge_is_a_load_error(tmp_path):
    p = _write(tmp_path, """
        start: a
        stages:
          - {id: a, owner: user, gate: {kind: artifact, files: [request.md]}, "on": {pass: nope}}
    """)
    with pytest.raises(dsl.FlowError, match="unknown stage"):
        dsl.load(p)


def test_non_terminal_stage_needs_a_pass_edge(tmp_path):
    p = _write(tmp_path, """
        start: a
        stages:
          - {id: a, owner: agent, gate: {kind: none}, "on": {fail: b}}
          - {id: b, owner: agent, terminal: true}
    """)
    with pytest.raises(dsl.FlowError, match="on.pass"):
        dsl.load(p)


def test_unreachable_terminal_is_a_load_error(tmp_path):
    p = _write(tmp_path, """
        start: a
        stages:
          - {id: a, owner: user, gate: {kind: none}, "on": {pass: a}}
          - {id: t, owner: agent, terminal: true}
    """)
    with pytest.raises(dsl.FlowError, match="unreachable"):
        dsl.load(p)


def test_unknown_gate_kind_is_a_load_error(tmp_path):
    p = _write(tmp_path, """
        start: a
        stages:
          - {id: a, owner: agent, gate: {kind: vibes}, "on": {pass: t}}
          - {id: t, owner: agent, terminal: true}
    """)
    with pytest.raises(dsl.FlowError, match="gate kind"):
        dsl.load(p)


def test_user_stage_cannot_have_an_action(tmp_path):
    p = _write(tmp_path, """
        start: a
        stages:
          - {id: a, owner: user, action: "userguide.flow.actions:compile_case", gate: {kind: none}, "on": {pass: t}}
          - {id: t, owner: agent, terminal: true}
    """)
    with pytest.raises(dsl.FlowError, match="user stage"):
        dsl.load(p)


def test_mermaid_renders_every_stage():
    m = dsl.load().mermaid()
    for sid in dsl.load().ids():
        assert sid in m


# --------------------------------------------------------------------------- registry


def test_specialists_have_overlapping_discussion_coverage():
    r = registry.Registry()
    p = r.partition()
    assert p["ok"] and p["design_fields"] == 28
    assert len(r.specialists) == 3
    assert len(p["shared"]) == 28  # overlap is deliberate, not field ownership


def test_discussion_perspectives_are_not_write_permissions():
    r = registry.Registry()
    assert r.check("wrf_physics", ["wps:dx", "input:history_interval"])["accepted"]


def test_unknown_specialist_is_an_error():
    with pytest.raises(registry.RegistryError):
        registry.Registry().check("nobody", ["wps:dx"])


def test_tool_whitelist_is_data():
    r = registry.Registry()
    assert r.can_use("research_design", "lookup_parameter")
    assert not r.can_use("research_design", "submit")


def test_context_slice_contains_owned_and_read_fields():
    r = registry.Registry()
    ctx = r.context("wrf_physics")
    assert "input:sf_urban_physics" in ctx["fields"]
    assert "input:bl_pbl_physics" in ctx["fields"]
    assert set(ctx["fields"]) <= set(r.all_ids())
