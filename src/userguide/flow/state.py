"""flow.state — the durable case journal and its guarantees.

Every row is appended to ``<case>/flow.jsonl`` and fsynced, so a crash mid-stage is recoverable: the journal,
not a mutable YAML file, is the state. Three guarantees are enforced here:

  * revision / CAS      a transition carries the revision it expects; two writers cannot both advance
  * claim leases        only the holder of a live claim may act (expired leases can be taken over)
  * attempt accounting  how many times a stage has been evaluated since it was entered

Rows: open | claim | release | gate | transition | note. ``current`` is derived from the last transition, so
replaying a journal always yields the same state (idempotent resume).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import fcntl
import secrets
import threading
from contextlib import contextmanager
from functools import wraps

_LOCAL = threading.local()

@contextmanager
def writer(case):
    """One local process/thread writes a case at a time, including action side effects."""
    case = Path(case).resolve()
    held = getattr(_LOCAL, "held", {})
    if case in held:
        yield
        return
    case.mkdir(parents=True, exist_ok=True)
    with (case / ".flow.lock").open("a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Conflict("case has an active writer") from exc
        _LOCAL.held = {**held, case: True}
        try:
            yield
        finally:
            _LOCAL.held = held
            fcntl.flock(fh, fcntl.LOCK_UN)

def locked(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        with writer(self.case):
            return fn(self, *args, **kwargs)
    return call
from pathlib import Path

LOG = "flow.jsonl"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts)


class FlowStateError(RuntimeError):
    """The journal cannot satisfy the request (bad state, missing open row, ...)."""


class Conflict(FlowStateError):
    """A concurrent writer, a stale revision or a foreign live claim."""


class Journal:
    """Append-only journal for one case directory."""

    def __init__(self, case: Path, start: str | None = None):
        self.case = Path(case)
        self.path = self.case / LOG
        self.start = start

    # ------------------------------------------------------------------ io

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    @locked
    def _append(self, row: dict) -> dict:
        row = {"seq": len(self.rows()) + 1, "at": _now(), **row}
        self.case.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return row

    # ------------------------------------------------------------------ derived state

    @property
    def revision(self) -> int:
        return sum(1 for r in self.rows() if r["kind"] == "transition")

    def current(self) -> str:
        for r in reversed(self.rows()):
            if r["kind"] == "transition":
                return r["to"]
        for r in self.rows():
            if r["kind"] == "open":
                return r["stage"]
        if not self.start:
            raise FlowStateError(f"case {self.case} has no journal and no start stage given")
        return self.start

    @locked
    def ensure_open(self, stage: str | None = None) -> dict:
        """Idempotent: write the ``open`` row once, at the first stage."""
        if any(r["kind"] == "open" for r in self.rows()):
            return next(r for r in self.rows() if r["kind"] == "open")
        return self._append({"kind": "open", "stage": stage or self.start or "request"})

    def entered_at(self, stage: str) -> int | None:
        """Seq of the last transition that entered ``stage`` (None if never)."""
        for r in reversed(self.rows()):
            if r["kind"] == "transition" and r["to"] == stage:
                return int(r["seq"])
        return None

    def attempts(self, stage: str) -> int:
        """Gate evaluations recorded for ``stage`` since it was last entered."""
        since = self.entered_at(stage) or 0
        return sum(1 for r in self.rows()
                   if r["kind"] == "gate" and r["stage"] == stage and int(r["seq"]) > since
                   and r["mode"] == "fail")

    def last_gate(self, stage: str) -> dict | None:
        for r in reversed(self.rows()):
            if r["kind"] == "gate" and r["stage"] == stage:
                return r
        return None

    def history(self, n: int = 10) -> list[dict]:
        return [r for r in self.rows() if r["kind"] in ("transition", "claim", "release", "note")][-n:]

    # ------------------------------------------------------------------ claims

    @locked
    def claim(self, actor: str, lease_seconds: int = 1800, token: str | None = None) -> dict:
        """Take (or renew) the lease. A live lease held by another actor is a conflict."""
        cur = self.claim_state()
        if cur and _parse(cur["expires_at"]) > _parse(_now()) and (cur["actor"] != actor or token != cur.get("token") or token is None):
            raise Conflict(f"case {self.case.name} is claimed by {cur['actor']} until {cur['expires_at']}")
        exp = (_parse(_now()) + dt.timedelta(seconds=int(lease_seconds))).isoformat(timespec="seconds")
        return self._append({"kind": "claim", "actor": actor, "expires_at": exp, "lease_seconds": lease_seconds, "token": token or secrets.token_urlsafe(32)})

    @locked
    def release(self, actor: str, note: str = "", token: str | None = None) -> dict:
        self.assert_claim(actor, token)
        return self._append({"kind": "release", "actor": actor, "note": note})

    def claim_state(self) -> dict | None:
        for r in reversed(self.rows()):
            if r["kind"] == "release":
                return None
            if r["kind"] == "claim":
                return r
        return None

    def assert_claim(self, actor: str, token: str | None = None) -> None:
        cur = self.claim_state()
        if cur is None:
            raise Conflict(f"no live claim on {self.case.name}; claim it before acting")
        if cur["actor"] != actor or token is None or token != cur.get("token"):
            raise Conflict(f"case {self.case.name} is claimed by {cur['actor']}, not {actor}")
        if _parse(cur["expires_at"]) <= _parse(_now()):
            raise Conflict(f"claim by {actor} expired at {cur['expires_at']}; re-claim to continue")

    # ------------------------------------------------------------------ writes

    @locked
    def gate(self, stage: str, mode: str, detail: str, actor: str, artifacts: dict | None = None) -> dict:
        return self._append({"kind": "gate", "stage": stage, "mode": mode, "detail": detail,
                             "actor": actor, "artifacts": artifacts or {}})

    @locked
    def transition(self, to: str, actor: str, outcome: str = "pass", note: str = "",
                   expected_revision: int | None = None) -> dict:
        """Append a transition. ``expected_revision`` is a compare-and-swap guard."""
        rev = self.revision
        if expected_revision is not None and expected_revision != rev:
            raise Conflict(f"revision mismatch: expected {expected_revision}, found {rev}")
        frm = self.current()
        return self._append({"kind": "transition", "from": frm, "to": to, "outcome": outcome,
                             "actor": actor, "note": note, "revision": rev + 1})

    @locked
    def note(self, actor: str, text: str) -> dict:
        return self._append({"kind": "note", "actor": actor, "text": text})

    # ------------------------------------------------------------------ view

    def status(self) -> dict:
        cur = self.current()
        rows = self.rows()
        seen = {r["to"] for r in rows if r["kind"] == "transition"} | \
               {r["from"] for r in rows if r["kind"] == "transition"}
        return {
            "case": str(self.case), "current": cur, "revision": self.revision,
            "attempts": self.attempts(cur), "last_gate": self.last_gate(cur),
            "claim": {k: v for k, v in (self.claim_state() or {}).items() if k != "token"} or None, "visited": sorted(seen),
            "transitions": [r for r in rows if r["kind"] == "transition"],
            "history": self.history(8),
        }
