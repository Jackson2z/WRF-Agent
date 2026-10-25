"""check_a_mundo — WRF's own start-up input checks, extracted as data.

``share/module_check_a_mundo.F`` is where wrf.exe inspects the namelist before integrating. It does two things:

  refuse    increments ``count_fatal_error`` (the run aborts at start-up)
  rewrite   assigns ``model_config_rec % <name> = ...`` (the value the user wrote is replaced, often only announced
            by a NOTE/WARNING line in rsl.error)

Both happen after the job has been queued. This module lists every refusal and every rewrite with the enclosing
IF conditions, the message text and the namelist variables involved, so the same conditions can be checked before
submission and the rewrites can be shown to the person who approved the configuration.

Two notions of "announced" are recorded per item, because they differ:

  message      text assigned to ``wrf_err_message`` near the branch (the branch *has* a message)
  emit_level   lowest level at which a call in the same IF branch prints something: 0 for ``wrf_message`` /
               ``wrf_error_fatal`` / ``wrf_debug(0, ...)``, n for ``wrf_debug(n, ...)``; None when no call in the
               branch prints. With the default ``debug_level = 0`` only emit_level 0 reaches rsl.error.
               Only a block IF branch (or CASE branch) counts: a single-line ``IF (...) x = y`` and a statement in the
               subroutine body have no branch of their own, so a print elsewhere is never attributed to them.
  nearby_emit_level  sensitivity bound: lowest print level of any call within NEARBY lines in the same subroutine,
               whatever branch it is in (catches a notice placed in an adjacent branch; may over-attribute).

    python3 -m src.userguide.check_a_mundo --src module_check_a_mundo.F --out check_a_mundo.json \
        --label wrf-model/WRF@v4.6.1:share/module_check_a_mundo.F --version 4.6.1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

VAR = re.compile(r"model_config_rec\s*%\s*([a-z0-9_]+)", re.I)
IF_THEN = re.compile(r"^\s*(ELSE\s*)?IF\s*\((.*)\)\s*THEN\s*$", re.I)
ELSE = re.compile(r"^\s*ELSE\s*$", re.I)
END_IF = re.compile(r"^\s*END\s*IF\b", re.I)
DO = re.compile(r"^\s*DO\b", re.I)
DO_VAR = re.compile(r"^\s*DO\s+([a-z][a-z0-9_]*)\s*=\s*([^,]+),\s*(.+?)\s*$", re.I)
LOCAL_ASSIGN = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*(\([^=]*\))?\s*=(?!=)\s*(.+)$", re.I)
NOT_LOCAL = {"wrf_err_message", "count_fatal_error", "model_config_rec"}
CPP = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(.*)$", re.I)
END_DO = re.compile(r"^\s*END\s*DO\b", re.I)
SUB = re.compile(r"^\s*(RECURSIVE\s+)?SUBROUTINE\s+([a-z0-9_]+)", re.I)
SELECT = re.compile(r"^\s*SELECT\s+CASE\b", re.I)
CASE = re.compile(r"^\s*CASE\b", re.I)
END_SELECT = re.compile(r"^\s*END\s*SELECT\b", re.I)
MSG = re.compile(r"wrf_err_message\s*=\s*(.+)$", re.I)
FATAL = re.compile(r"count_fatal_error\s*=\s*count_fatal_error\s*\+\s*1", re.I)
ASSIGN = re.compile(r"^\s*model_config_rec\s*%\s*([a-z0-9_]+)\s*(\([^=]*\))?\s*=(?!=)\s*(.+)$", re.I)
ONE_LINE_IF = re.compile(r"^\s*IF\s*\((.*)\)\s*(model_config_rec.*|count_fatal_error.*)$", re.I)
EMIT_ALWAYS = re.compile(r"\bCALL\s+wrf_(message|error_fatal)\s*\(", re.I)
EMIT_DEBUG = re.compile(r"\bCALL\s+wrf_debug\s*\(\s*(\d+)\s*,", re.I)
NEARBY = 10


def _logical_lines(text: str):
    """Join Fortran free-form continuation lines (trailing '&'); keep the first physical line number."""
    buf, start = "", None
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.split("!", 1)[0] if not raw.lstrip().startswith(("'", '"')) else raw
        line = line.rstrip()
        if start is None:
            start = i
        cont = line.endswith("&")
        piece = line[:-1] if cont else line
        buf += (" " if buf else "") + piece.strip().lstrip("&").strip()
        if not cont:
            yield start, buf
            buf, start = "", None


def _clean_msg(s: str) -> str:
    s = s.strip()
    parts = re.findall(r"'([^']*)'|\"([^\"]*)\"", s)
    return "".join(a or b for a, b in parts).strip() or s


def extract(src: Path, label: str | None = None, version: str | None = None) -> dict:
    text = src.read_text(encoding="latin-1")
    stack: list[str] = []       # enclosing IF conditions
    blocks: list[int] = [0]     # innermost IF/CASE branch id; the first entry is the subroutine body
    body_ids: set[int] = {0}
    case_ids: set[int] = set()
    next_block = 1
    emits: dict[int, list[int]] = {}   # branch id -> print levels of calls in that branch
    calls: list[tuple[str | None, int, int]] = []   # (subroutine, line, level) of every print call
    sub = None
    last_msgs: list[str] = []
    items = []
    # exact branch conditions for evaluation: chain[k] is the full condition of the k-th enclosing branch, including
    # the negation of every earlier sibling of an ELSE IF / ELSE (``stack`` keeps the historical, shorter form)
    chain: list[str] = []
    siblings: list[list[str]] = []
    loops: list[dict] = []
    # preprocessor: cpp[k] = condition of the k-th open #if branch (C preprocessor syntax); cpp_prior = earlier
    # sibling conditions of that #if, so #elif / #else become their exact negations
    cpp: list[str] = []
    cpp_prior: list[list[str]] = []
    cpp_events_at: dict[int, tuple] = {}
    local_vars: list[dict] = []        # assignments to local variables (d1_value, oops, ...), used by evaluators
    for ln, raw_line in enumerate(text.splitlines(), 1):
        mc = CPP.match(raw_line)
        if not mc:
            continue
        kw, expr = mc.group(1).lower(), mc.group(2).split("!", 1)[0].strip() if mc.group(1).lower() != "if" else mc.group(2).strip()
        if kw in ("if", "ifdef", "ifndef"):
            cond = (f"defined({expr})" if kw == "ifdef" else f"!defined({expr})" if kw == "ifndef" else f"({expr})")
            cpp_events_at[ln] = ("open", cond)
        elif kw == "elif":
            cpp_events_at[ln] = ("elif", f"({expr})")
        elif kw == "else":
            cpp_events_at[ln] = ("else", None)
        else:
            cpp_events_at[ln] = ("close", None)
    for ln, line in _logical_lines(text):
        ev = cpp_events_at.get(ln)
        if ev:
            kind, cond = ev
            if kind == "open":
                cpp.append(cond)
                cpp_prior.append([cond])
            elif kind == "elif" and cpp:
                cpp[-1] = " && ".join([f"!{p}" for p in cpp_prior[-1]] + [cond])
                cpp_prior[-1].append(cond)
            elif kind == "else" and cpp:
                cpp[-1] = " && ".join(f"!{p}" for p in cpp_prior[-1])
            elif kind == "close" and cpp:
                cpp.pop()
                cpp_prior.pop()
            continue
        if not line or line.lstrip().startswith("#"):
            continue
        m = SUB.match(line)
        if m:
            sub, stack, last_msgs = m.group(2), [], []
            chain, siblings, loops = [], [], []
            blocks, next_block = [next_block], next_block + 1
            body_ids.add(blocks[0])
            continue
        m = IF_THEN.match(line)
        if m:
            cond = m.group(2).strip()
            if m.group(1):              # ELSE IF: replace the innermost condition
                if stack:
                    stack.pop()
                if len(blocks) > 1:
                    blocks.pop()
                prior = siblings[-1] if siblings else []
                if chain:
                    chain.pop()
                chain.append(" .AND. ".join([f".NOT. ({p})" for p in prior] + [f"({cond})"]))
                if siblings:
                    siblings[-1].append(cond)
            else:
                chain.append(f"({cond})")
                siblings.append([cond])
            stack.append(cond)
            blocks.append(next_block)
            next_block += 1
            last_msgs = []
            continue
        if ELSE.match(line):
            if stack:
                stack[-1] = f"NOT ({stack[-1]})"
            if chain and siblings:
                chain[-1] = " .AND. ".join(f".NOT. ({p})" for p in siblings[-1])
            if len(blocks) > 1:
                blocks[-1] = next_block
                next_block += 1
            last_msgs = []
            continue
        if END_IF.match(line):
            if stack:
                stack.pop()
            if chain:
                chain.pop()
            if siblings:
                siblings.pop()
            if len(blocks) > 1:
                blocks.pop()
            last_msgs = []
            continue
        md = DO_VAR.match(line)
        if md:
            loops.append({"var": md.group(1).lower(), "start": md.group(2).strip(), "end": md.group(3).strip()})
            continue
        if END_DO.match(line):
            if loops:
                loops.pop()
            continue
        if SELECT.match(line):          # a SELECT opens a region; each CASE inside it is a branch
            blocks.append(-next_block)
            next_block += 1
            continue
        if CASE.match(line):
            if blocks[-1] in case_ids:   # next CASE closes the previous branch
                blocks.pop()
            blocks.append(next_block)
            case_ids.add(next_block)
            next_block += 1
            continue
        if END_SELECT.match(line):
            if blocks[-1] in case_ids:
                blocks.pop()
            if len(blocks) > 1 and blocks[-1] < 0:
                blocks.pop()
            continue
        if EMIT_ALWAYS.search(line):
            emits.setdefault(blocks[-1], []).append(0)
            calls.append((sub, ln, 0))
        else:
            md = EMIT_DEBUG.search(line)
            if md:
                emits.setdefault(blocks[-1], []).append(int(md.group(1)))
                calls.append((sub, ln, int(md.group(1))))
        m = MSG.match(line)
        if m:
            last_msgs.append(_clean_msg(m.group(1)))
            continue
        cond_extra = None
        m1 = ONE_LINE_IF.match(line)
        body = line
        if m1:
            cond_extra, body = m1.group(1).strip(), m1.group(2)
        conds = stack + ([cond_extra] if cond_extra else [])
        own = None if (m1 or blocks[-1] in body_ids or blocks[-1] < 0) else blocks[-1]
        exact = chain + ([f"({cond_extra})"] if cond_extra else [])
        if FATAL.search(body):
            items.append(_item("refuse", ln, sub, conds, last_msgs, None, None))
            items[-1].update({"_block": own, "chain": exact, "loops": [dict(x) for x in loops], "cpp": list(cpp)})
            continue
        m = ASSIGN.match(body)
        if m:
            items.append(_item("rewrite", ln, sub, conds, last_msgs, m.group(1).lower(), m.group(3).strip()))
            items[-1].update({"_block": own, "chain": exact, "loops": [dict(x) for x in loops], "cpp": list(cpp),
                              "target_index": (m.group(2) or "").strip("() ") or None})
            continue
        m = LOCAL_ASSIGN.match(body)
        if m and m.group(1).lower() not in NOT_LOCAL:
            local_vars.append({"line": ln, "subroutine": sub, "name": m.group(1).lower(),
                               "index": (m.group(2) or "").strip("() ") or None, "value": m.group(3).strip(),
                               "chain": exact, "loops": [dict(x) for x in loops], "cpp": list(cpp)})
    for it in items:
        b = it.pop("_block")
        levels = emits.get(b, []) if b is not None else []
        it["emit_level"] = min(levels) if levels else None
        near = [lv for s, l, lv in calls if s == it["subroutine"] and abs(l - it["line"]) <= NEARBY]
        it["nearby_emit_level"] = min(near) if near else None
    raw = text.splitlines()
    for it in items:
        if it["message"]:
            continue
        back = []
        for j in range(it["line"] - 2, max(-1, it["line"] - 16), -1):
            r = raw[j]
            if r.strip().startswith("!---") or SUB.match(r):
                break
            m = MSG.search(r.strip())
            if m:
                back.append(_clean_msg(m.group(1)))
        if back:
            it["message"] = " ".join(reversed(back))[:400]
            u = it["message"].upper()
            it["message_severity"] = ("error" if "ERROR" in u else "warning" if "WARN" in u
                                      else "note" if "NOTE" in u else "none")
    n_ref = sum(1 for i in items if i["kind"] == "refuse")
    return {"source": label or src.name, "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
            "wrf_version": version, "refusals": n_ref, "rewrites": len(items) - n_ref, "items": items,
            "locals": local_vars,
            "generated_by": "src/userguide/check_a_mundo.py"}


def _item(kind, ln, sub, conds, msgs, target, value):
    used = sorted({v.lower() for c in conds for v in VAR.findall(c)} | ({target} if target else set()))
    text = " ".join(msgs)
    severity = ("error" if "ERROR" in text.upper() else "warning" if "WARN" in text.upper()
                else "note" if "NOTE" in text.upper() else "none")
    return {"kind": kind, "line": ln, "subroutine": sub, "conditions": conds[-3:], "message": text[:400],
            "message_severity": severity, "target": target, "value": value, "params": used}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--label", help="neutral source label written into the ledger (no local paths)")
    ap.add_argument("--version", help="WRF release the source belongs to")
    a = ap.parse_args(argv)
    res = extract(a.src, a.label, a.version)
    a.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("refusals", "rewrites")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
