"""Deterministic local runner. Actions may repeat after a crash; no exactly-once claim."""
from pathlib import Path
from . import actions, dsl, gates
from .state import Journal, writer
from .gates import _module


def _action(target):
    mod, fn = target.split(":", 1)
    return getattr(_module(mod), fn)


def status(case, flow=None):
    flow = flow or dsl.load()
    j = Journal(case, start=flow.start)
    st = flow.stage(j.current())
    return {**j.status(), "stage": st.as_dict(), "owner": st.owner, "terminal": st.terminal,
            "stages": [s.as_dict() for s in flow.stages]}


def run(case, flow=None, actor="agent", max_steps=32, lease_seconds=1800, dry_run=False):
    case = Path(case)
    flow = flow or dsl.load()
    if dry_run:
        return _run(case, flow, actor, max_steps, True)
    with writer(case):
        # Legacy state is explicitly migrated by the dashboard reader before opening a new journal.
        from ..dashboard import load_state
        load_state(case)
        return _run(case, flow, actor, max_steps, False)


def _run(case, flow, actor, max_steps, dry_run):
    j = Journal(case, start=flow.start)
    steps = []
    claim = j.claim_state()
    if claim and __import__('datetime').datetime.fromisoformat(claim['expires_at']) > __import__('datetime').datetime.now(__import__('datetime').timezone.utc):
        from .state import Conflict
        raise Conflict('case has an active claim; token required for takeover')
    if not dry_run:
        j.ensure_open(flow.start)
    def result(reason, detail=""):
        return {"status": reason, "stage": j.current(), "detail": detail, "steps": steps,
                "attempts": j.attempts(j.current()), "revision": j.revision}
    for _ in range(max_steps):
        cur = j.current()
        st = flow.stage(cur)
        rev = j.revision
        if st.terminal:
            return result("failed" if cur == "failed" else "done")
        # A user-owned gate is informational, never an automatic approval transition.
        # Only dashboard.submit records the explicit human approval event.
        if st.owner == "user":
            if dry_run:
                return result("dry_run", "user action required")
            if st.advance_on_artifact:
                mode, detail = gates.evaluate(st.gate, case)
                if mode == gates.PASS:
                    j.gate(cur, mode, detail, actor="user")
                    j.transition(flow.next(cur, "pass"), actor="user", note=f"{cur}: {detail}",
                                 expected_revision=rev)
                    steps.append({"stage": cur, "mode": mode, "to": flow.next(cur, "pass"), "detail": detail})
                    continue
                return result("waiting_user", detail)
            return result("waiting_user", "user action required" if cur != "review" else
                          actions.gate_spec_approved(case)["detail"])
        if cur in ("compile", "launch", "monitor", "verify", "restart"):
            approval = actions.gate_spec_approved(case)
            if not approval["ok"]:
                if not dry_run:
                    j.transition("review", actor, outcome="fail", note=approval["detail"], expected_revision=rev)
                return result("waiting_user", approval["detail"])
        if st.owner != "user" and j.attempts(cur) >= st.max_attempts:
            return result("blocked", "persistent failure budget exhausted; human intervention required")
        try:
            if st.action:
                if dry_run:
                    return result("dry_run", st.action)
                act = _action(st.action)(case)
                steps.append({"stage": cur, "action": st.action, **act})
                mode, detail = gates._normalise(act)
                if mode == gates.PASS:
                    mode, detail = gates.evaluate(st.gate, case)
            else:
                mode, detail = gates.evaluate(st.gate, case)
        except Exception as exc:
            mode, detail = gates.FAIL, f"{type(exc).__name__}: {exc}"
        if dry_run:
            return {**result("dry_run", detail), "mode": mode}
        j.gate(cur, mode, detail, actor)
        steps.append({"stage": cur, "mode": mode, "detail": detail})
        if mode == gates.WAIT:
            return result("waiting_user" if cur == "restart" else "waiting_external", detail)
        if mode == gates.FAIL and j.attempts(cur) < st.max_attempts:
            continue
        nxt = flow.next(cur, mode)
        if not nxt or nxt == cur:
            return result("blocked", detail)
        if mode == gates.FAIL and cur in ("compile", "launch"):
            actions.invalidate_approval(case, f"{cur}: {detail}")
        j.transition(nxt, actor, outcome=mode, note=detail, expected_revision=rev)
    return result("max_steps")


def resume(case, **kw):
    return run(case, **kw)


def board_rows(case):
    from . import board
    return board.rows(case)
