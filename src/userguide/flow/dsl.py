"""flow.dsl — the stage graph, validated at load.

A stage declares who acts (user | agent | compiler | automation), what automatic work it performs
(action, a "module:function" string), what must be true to leave it (gate, executable), which files it
produces (artifacts) and where each outcome goes (on: {pass, fail}).

Validation is deliberately strict: a dangling edge, a missing pass edge, an unreachable terminal state or
a gate that no evaluator understands is a load-time error, not a runtime surprise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
STAGES = HERE / "stages.yaml"

OWNERS = {"user", "agent", "compiler", "automation"}
GATE_KINDS = {"artifact", "python", "command", "all", "none"}
OUTCOMES = {"pass", "fail"}


class FlowError(ValueError):
    """The stage graph is not loadable."""


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    owner: str
    gate: dict = field(default_factory=dict)
    action: str | None = None
    artifacts: tuple[str, ...] = ()
    on: dict = field(default_factory=dict)
    max_attempts: int = 2
    terminal: bool = False
    advance_on_artifact: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "owner": self.owner, "gate": self.gate,
                "action": self.action, "artifacts": list(self.artifacts), "on": self.on,
                "max_attempts": self.max_attempts, "terminal": self.terminal,
                "advance_on_artifact": self.advance_on_artifact}


def _gate(raw: dict | None, sid: str) -> dict:
    if not raw:
        return {"kind": "none"}
    kind = str(raw.get("kind", "")).strip()
    if kind not in GATE_KINDS:
        raise FlowError(f"stage {sid!r}: gate kind {kind!r} not one of {sorted(GATE_KINDS)}")
    if kind == "artifact" and not raw.get("files"):
        raise FlowError(f"stage {sid!r}: artifact gate without files")
    if kind in ("python", "command") and not (raw.get("target") or raw.get("cmd")):
        raise FlowError(f"stage {sid!r}: {kind} gate without target/cmd")
    if kind == "all" and not raw.get("gates"):
        raise FlowError(f"stage {sid!r}: all-gate without gates")
    return {"kind": kind, **{k: v for k, v in raw.items() if k != "kind"}}


class Flow:
    """An immutable, validated stage graph."""

    def __init__(self, stages: list[Stage], start: str | None = None, source: Path | None = None):
        self.stages = tuple(stages)
        self.by_id = {s.id: s for s in self.stages}
        self.start = start or self.stages[0].id
        self.source = source
        self._validate()

    # ------------------------------------------------------------------ lookups

    def stage(self, sid: str) -> Stage:
        try:
            return self.by_id[sid]
        except KeyError:
            raise FlowError(f"unknown stage {sid!r} (known: {sorted(self.by_id)})") from None

    def __getitem__(self, sid: str) -> Stage:
        return self.stage(sid)

    def __iter__(self):
        return iter(self.stages)

    def __len__(self) -> int:
        return len(self.stages)

    def ids(self) -> list[str]:
        return [s.id for s in self.stages]

    def next(self, sid: str, outcome: str = "pass") -> str | None:
        """Target stage for an outcome, or None when the outcome has no edge (a deliberate stop)."""
        st = self.stage(sid)
        if outcome not in OUTCOMES:
            raise FlowError(f"outcome {outcome!r} not in {sorted(OUTCOMES)}")
        return st.on.get(outcome)

    def terminals(self) -> list[str]:
        return [s.id for s in self.stages if s.terminal]

    # ------------------------------------------------------------------ validation

    def _validate(self) -> None:
        if not self.stages:
            raise FlowError("no stages")
        seen, dupes = set(), set()
        for s in self.stages:
            (dupes if s.id in seen else seen).add(s.id)
            if s.owner not in OWNERS:
                raise FlowError(f"stage {s.id!r}: owner {s.owner!r} not one of {sorted(OWNERS)}")
            if s.terminal and s.on:
                raise FlowError(f"stage {s.id!r}: terminal stage must not declare on: edges")
            if not s.terminal and "pass" not in s.on:
                raise FlowError(f"stage {s.id!r}: non-terminal stage needs an on.pass edge")
            if s.max_attempts < 1:
                raise FlowError(f"stage {s.id!r}: max_attempts must be >= 1")
            if s.owner == "user" and s.action:
                raise FlowError(f"stage {s.id!r}: a user stage must not have a machine action")
            if s.advance_on_artifact and (s.owner != "user" or s.gate.get("kind") != "artifact"):
                raise FlowError(f"stage {s.id!r}: advance_on_artifact is only for user stages gated on an artefact")
        if dupes:
            raise FlowError(f"duplicate stage ids: {sorted(dupes)}")
        if self.start not in self.by_id:
            raise FlowError(f"start stage {self.start!r} unknown")
        for s in self.stages:
            for outcome, target in s.on.items():
                if outcome not in OUTCOMES:
                    raise FlowError(f"stage {s.id!r}: unknown outcome {outcome!r}")
                if target not in self.by_id:
                    raise FlowError(f"stage {s.id!r}: on.{outcome} -> unknown stage {target!r}")
        if not self.terminals():
            raise FlowError("no terminal stage")
        reachable = self._reachable()
        for t in self.terminals():
            if t not in reachable:
                raise FlowError(f"terminal stage {t!r} is unreachable from {self.start!r}")

    def _reachable(self) -> set[str]:
        seen, todo = set(), [self.start]
        while todo:
            cur = todo.pop()
            if cur in seen:
                continue
            seen.add(cur)
            todo.extend(self.stage(cur).on.values())
        return seen

    # ------------------------------------------------------------------ render

    def mermaid(self) -> str:
        lines = ["graph LR"]
        for s in self.stages:
            shape = f"{s.id}([{s.title}])" if s.terminal else f"{s.id}[{s.title}]"
            lines.append(f"  {shape}:::owner_{s.owner}")
            if s.terminal:
                continue
            for outcome, target in s.on.items():
                arrow = "-->" if outcome == "pass" else "-.->"
                lines.append(f"  {s.id} {arrow}|{outcome}| {target}")
        lines.append("  classDef owner_user fill:#fee,stroke:#a33")
        lines.append("  classDef owner_agent fill:#eef,stroke:#33a")
        lines.append("  classDef owner_compiler fill:#efe,stroke:#3a3")
        lines.append("  classDef owner_automation fill:#f5f5f5,stroke:#666")
        return "\n".join(lines)


def load(path: Path | None = None) -> Flow:
    """Load and validate a stage graph."""
    p = Path(path or STAGES)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    stages = []
    for row in raw.get("stages") or []:
        stages.append(Stage(
            id=str(row["id"]), title=str(row.get("title", row["id"])), owner=str(row.get("owner", "agent")),
            gate=_gate(row.get("gate"), str(row["id"])), action=row.get("action"),
            artifacts=tuple(row.get("artifacts") or ()),
            on={str(k): str(v) for k, v in (row.get("on") or row.get(True) or {}).items()},
            max_attempts=int(row.get("max_attempts", 2)), terminal=bool(row.get("terminal", False)),
            advance_on_artifact=bool(row.get("advance_on_artifact", False)),
        ))
    return Flow(stages, start=raw.get("start"), source=p)
