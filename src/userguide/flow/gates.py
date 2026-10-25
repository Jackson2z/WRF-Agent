"""flow.gates — executable stage gates.

A gate answers one question with three possible answers:

  pass  the stage's acceptance condition holds
  fail  it does not (the runner retries, then takes the fail edge)
  wait  it cannot be answered yet because something outside the pipeline has not happened
        (the user has not submitted, the run is still writing frames, ...) — no attempt is charged

Kinds: ``artifact`` (files exist), ``python`` (``module:function`` returning bool or {ok, detail, wait}),
``command`` (exit status of a local command), ``all`` (conjunction), ``none`` (always pass).
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

PASS, FAIL, WAIT = "pass", "fail", "wait"


def _normalise(result) -> tuple[str, str]:
    if isinstance(result, bool):
        return (PASS if result else FAIL), ""
    if isinstance(result, tuple):
        mode, detail = result
        return str(mode), str(detail)
    if isinstance(result, dict):
        detail = str(result.get("detail", ""))
        if result.get("wait"):
            return WAIT, detail or "waiting for an external event"
        return (PASS if result.get("ok") else FAIL), detail
    raise TypeError(f"gate returned {type(result).__name__}; expected bool, (mode, detail) or dict")


RELATIVE = {"actions", "board", "registry", "runner", "gates", "state", "dsl"}


def _module(name: str):
    """Resolve a target module: relative to this package first, then top-level, then a src.-rooted tree."""
    if name in RELATIVE:
        return importlib.import_module(f"{__package__}.{name}")
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return importlib.import_module(f"src.{name}")


def _call(target: str, case: Path):
    if ":" not in target:
        raise ValueError(f"python gate target must be 'module:function', got {target!r}")
    mod, fn = target.split(":", 1)
    return getattr(_module(mod), fn)(Path(case))


def evaluate(gate: dict, case: Path, timeout: int = 120) -> tuple[str, str]:
    """-> (mode, detail) with mode in {pass, fail, wait}."""
    kind = str((gate or {}).get("kind", "none"))
    case = Path(case)

    if kind == "none":
        return PASS, "no gate"
    if kind == "artifact":
        missing, empty = [], []
        for pattern in gate.get("files") or []:
            hits = sorted(p for p in case.glob(str(pattern)) if p.is_file())
            if not hits:
                missing.append(str(pattern))
            elif gate.get("nonempty", True) and all(p.stat().st_size == 0 for p in hits):
                empty.append(str(pattern))
        if missing or empty:
            return FAIL, f"missing artifacts: {missing}" + (f"; empty: {empty}" if empty else "")
        return PASS, f"{len(gate.get('files') or [])} artifact pattern(s) present"
    if kind == "python":
        return _normalise(_call(str(gate["target"]), case))
    if kind == "command":
        cmd = gate["cmd"]
        proc = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=str(case), capture_output=True,
                              text=True, timeout=int(gate.get("timeout", timeout)))
        tail = (proc.stdout or "").strip().splitlines()[-3:] + (proc.stderr or "").strip().splitlines()[-3:]
        return (PASS if proc.returncode == 0 else FAIL), f"rc={proc.returncode} " + " | ".join(tail)
    if kind == "all":
        details, worst = [], PASS
        for sub in gate.get("gates") or []:
            mode, detail = evaluate(sub, case, timeout)
            details.append(f"{sub.get('kind')}:{mode} {detail}".strip())
            if mode == FAIL:
                worst = FAIL
            elif mode == WAIT and worst == PASS:
                worst = WAIT
        return worst, "; ".join(details)
    raise ValueError(f"unknown gate kind {kind!r}")
