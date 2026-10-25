"""chat — the in-page agent: an OpenAI-compatible tool-calling loop over agent_tools, one session per case.

Design borrowed from DeepSeek Harness (dsh): the session is an append-only JSONL log in the case directory
(chat.jsonl) so the conversation survives reloads; tool calls are recorded as their own rows and rendered
as collapsible cards; approval stays with the user (no submit tool).

Model endpoint (OpenAI-compatible), from the environment:
    WRF_AGENT_LLM_BASE_URL   default http://127.0.0.1:3425/v1
    WRF_AGENT_LLM_API_KEY    required
    WRF_AGENT_LLM_MODEL      default dim/deepseek-v4.1-flash
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from . import agent_tools as T

MAX_STEPS = 12

SYSTEM = """You are WRF-Agent, a team of WRF (v4.6.1) modelling experts working with a user in a web dashboard.
The dashboard shows the workflow: Request -> Decompose -> Review -> Compile -> Launch -> Monitor -> Verify -> Restart.

Your job:
1. Understand what the user wants to simulate. Record it with set_request (user's own words).
2. Decompose it into the design fields (call design_fields to see them). Propose a value AND a reason for every
   design field with set_draft. Per-domain fields take comma-separated values, one per domain.
3. The compiler derives everything else (time_step, nest geometry in WRF, dates, o3input, ...) and checks hard rules.
   After set_draft, read the returned check; fix FAIL items yourself before handing over.
4. Things the compiler cannot decide are the user's call: intent, physics choices, study design. Ask short,
   Socratic questions about those, one or two at a time, and explain trade-offs in plain language.
5. Use lookup_parameter to quote the Users' Guide when explaining a parameter.
6. Fields with edited_by: user were changed by the user in the form; keep them unless the user asks you to change them,
   and mention it if you think the edit causes a problem.
7. You never submit. When the draft is ready, tell the user to review the left panel and press Submit.

Reply in the language the user writes in. Be concise."""


def _log(case: Path) -> Path:
    return case / "chat.jsonl"


def history(case: Path) -> list[dict]:
    p = _log(case)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(case: Path, row: dict) -> dict:
    row = {"time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **row}
    with _log(case).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _to_messages(rows: list[dict]) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    for r in rows:
        if r["role"] == "user":
            msgs.append({"role": "user", "content": r["content"]})
        elif r["role"] == "assistant":
            m = {"role": "assistant", "content": r.get("content") or ""}
            if r.get("tool_calls"):
                m["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                                   for c in r["tool_calls"]]
            msgs.append(m)
        elif r["role"] == "tool":
            msgs.append({"role": "tool", "tool_call_id": r["tool_call_id"],
                         "content": json.dumps(r["result"], ensure_ascii=False, default=str)[:20000]})
    return msgs


def settings() -> dict:
    return {"base_url": os.environ.get("WRF_AGENT_LLM_BASE_URL", "http://127.0.0.1:3425/v1"),
            "model": os.environ.get("WRF_AGENT_LLM_MODEL", "dim/deepseek-v4.1-flash"),
            "has_key": bool(os.environ.get("WRF_AGENT_LLM_API_KEY"))}


def send(case: Path, text: str, model: str | None = None, client=None) -> list[dict]:
    """Append the user's message, run the tool loop, return the new rows."""
    from openai import OpenAI

    s = settings()
    client = client or OpenAI(base_url=s["base_url"], api_key=os.environ.get("WRF_AGENT_LLM_API_KEY") or "none")
    model = model or s["model"]
    new = [_append(case, {"role": "user", "content": text})]
    tools = [{"type": "function", "function": spec} for spec in T.SPECS]
    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(model=model, messages=_to_messages(history(case)), tools=tools)
        msg = resp.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "args": json.loads(c.function.arguments or "{}")}
                 for c in (msg.tool_calls or [])]
        new.append(_append(case, {"role": "assistant", "content": msg.content or "", "tool_calls": calls, "model": model}))
        if not calls:
            return new
        for c in calls:
            try:
                result = T.call(case, c["name"], c["args"])
            except Exception as e:  # noqa: BLE001 - return the error to the model
                result = {"error": f"{type(e).__name__}: {e}"}
            new.append(_append(case, {"role": "tool", "tool_call_id": c["id"], "name": c["name"], "args": c["args"], "result": result}))
    new.append(_append(case, {"role": "assistant", "content": f"(stopped after {MAX_STEPS} tool steps)", "tool_calls": []}))
    return new
