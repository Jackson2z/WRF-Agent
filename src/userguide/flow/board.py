"""flow.board — the handoff contract between specialists.

Specialists do not talk in prose: each contribution is a digest with five keys, appended to
``<case>/board.jsonl``. The keys are the same ones the pipeline blackboard uses elsewhere, because the
questions a downstream specialist must be able to ask are always the same five:

    key_findings              what I established (values, with the reason)
    claims_made              what I want the case to assert downstream
    deviations_from_outline   where I departed from the request or the plan, and why
    open_questions            what a person must decide (the human surface)
    downstream_flags         what the next specialist must re-check because of me

Validation is strict: a digest missing a key, carrying an unknown key, or with no findings is rejected. An
empty list is a statement ("nothing to report"); a missing key is a broken contract.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

BOARD = "board.jsonl"
KEYS = ("key_findings", "claims_made", "deviations_from_outline", "open_questions", "downstream_flags")
NON_EMPTY = ("key_findings",)
ACTORS_OK = {"user", "agent", "conductor", "compiler", "automation"}


class BoardError(ValueError):
    """The digest does not satisfy the handoff contract."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def validate(digest: dict, registry=None, actor: str | None = None) -> dict:
    if not isinstance(digest, dict):
        raise BoardError(f"digest must be a mapping, got {type(digest).__name__}")
    unknown = sorted(set(digest) - set(KEYS))
    if unknown:
        raise BoardError(f"unknown digest keys {unknown}; allowed: {list(KEYS)}")
    missing = [k for k in KEYS if k not in digest]
    if missing:
        raise BoardError(f"missing digest keys {missing}; every key is required (empty list = nothing to report)")
    out = {}
    for k in KEYS:
        items = digest[k]
        if not isinstance(items, (list, tuple)):
            raise BoardError(f"digest[{k!r}] must be a list, got {type(items).__name__}")
        for it in items:
            if not isinstance(it, (str, dict)):
                raise BoardError(f"digest[{k!r}] items must be strings or mappings")
        out[k] = list(items)
    for k in NON_EMPTY:
        if not out[k]:
            raise BoardError(f"digest[{k!r}] must not be empty")
    if registry is not None and actor and actor not in ACTORS_OK:
        if actor not in registry.by_id:
            raise BoardError(f"actor {actor!r} is neither a known role {sorted(ACTORS_OK)} "
                             f"nor a specialist {sorted(registry.by_id)}")
    return out


def post(case: Path, actor: str, digest: dict, registry=None, task: str | None = None,
         artifact: Path | None = None) -> dict:
    """Validate and append one digest. Returns the stored row."""
    case = Path(case)
    d = validate(digest, registry=registry, actor=actor)
    if artifact is not None and not Path(artifact).exists():
        raise BoardError(f"artifact {artifact} does not exist; register it in the ledger first")
    row = {"at": _now(), "actor": actor, "task": task, "artifact": str(artifact) if artifact else None, "digest": d}
    case.mkdir(parents=True, exist_ok=True)
    with (case / BOARD).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def rows(case: Path) -> list[dict]:
    p = Path(case) / BOARD
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest(case: Path, actor: str | None = None) -> dict | None:
    for row in reversed(rows(case)):
        if actor is None or row["actor"] == actor:
            return row
    return None


def open_questions(case: Path) -> list[dict]:
    """The human surface as the specialists see it: every open question, with who raised it."""
    out = []
    for row in rows(case):
        for q in row["digest"]["open_questions"]:
            out.append({"actor": row["actor"], "question": q, "at": row["at"]})
    return out


def deviations(case: Path) -> list[dict]:
    out = []
    for row in rows(case):
        for d in row["digest"]["deviations_from_outline"]:
            out.append({"actor": row["actor"], "deviation": d, "at": row["at"]})
    return out


def downstream_flags(case: Path) -> list[dict]:
    out = []
    for row in rows(case):
        for f in row["digest"]["downstream_flags"]:
            out.append({"actor": row["actor"], "flag": f, "at": row["at"]})
    return out


def findings(case: Path) -> list[dict]:
    out = []
    for row in rows(case):
        for f in row["digest"]["key_findings"]:
            out.append({"actor": row["actor"], "finding": f, "at": row["at"]})
    return out
