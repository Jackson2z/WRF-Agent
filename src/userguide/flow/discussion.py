"""Bounded deliberation before approval; no LLM is used by the execution runner.

Three independent proposals -> cross-review -> synthesis with preserved dissent -> human selection.
Each model request uses a fresh message list. Only select writes draft.yaml; only the
separate dashboard Submit endpoint approves experiment.yaml.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import yaml

from .. import chat, checks
from . import actions, registry
from .state import Journal, writer


class DiscussionError(ValueError):
    pass


def _once(client, model, messages):
    response = client.chat.completions.create(
        model=model, messages=messages, response_format={"type": "json_object"})
    usage = getattr(response, "usage", None)
    tokens = {"prompt_tokens": getattr(usage, "prompt_tokens", None),
              "completion_tokens": getattr(usage, "completion_tokens", None)} if usage else None
    return response.choices[0].message.content, tokens


def _answer(client, model, system, payload, check=None, repairs=1, raw=None):
    """Ask for JSON, validate, and repair at most ``repairs`` times by quoting the error back.

    Real models do drop fields or write free prose where a value belongs; failing the whole discussion on the
    first miss wastes the run. The repair round is bounded and the last raw output is kept for audit.
    """
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    last = None
    for attempt in range(repairs + 1):
        t0 = time.perf_counter()
        content, tokens = _once(client, model, messages)
        elapsed = round(time.perf_counter() - t0, 3)
        last = content
        if raw is not None:
            raw.append({"attempt": attempt, "content": (content or "")[:6000],
                        "elapsed_s": elapsed, "tokens": tokens})
        try:
            result = json.loads(content or "")
            if not isinstance(result, dict):
                raise DiscussionError("model output is not a JSON object")
            if check is not None:
                check(result)
            return result
        except (DiscussionError, json.JSONDecodeError) as exc:
            if attempt == repairs:
                raise DiscussionError(f"{type(exc).__name__}: {exc}") from None
            messages = messages + [
                {"role": "assistant", "content": content or ""},
                {"role": "user", "content": "That output was rejected: " + str(exc) +
                 ". Return the same JSON object again, corrected, with no commentary."}]
    raise DiscussionError("unreachable")


def _list(obj, key):
    if not isinstance(obj.get(key), list):
        raise DiscussionError(f"{key} must be a list")


def _design(value, ids, require_all=False):
    if not isinstance(value, dict) or not value:
        raise DiscussionError("design must be a non-empty object mapping field_id to {value, reason}")
    unknown = sorted(set(value) - set(ids))
    if unknown:
        raise DiscussionError(f"design contains unknown field ids: {unknown[:8]}")
    bad = [k for k, item in value.items()
           if not isinstance(item, dict) or item.get("value") in (None, "")
           or not str(item.get("reason") or "").strip()]
    if bad:
        raise DiscussionError(f"these ids need a non-empty value and a reason: {bad[:8]}")
    if require_all:
        missing = [k for k in ids if k not in value]
        if missing:
            raise DiscussionError(f"every design id is required; missing: {missing[:8]}")


def _at_decompose(case: Path) -> None:
    """Deliberation is the work of stage 2; elsewhere it would rewrite a decided plan."""
    from .state import Journal
    stage = Journal(case, "request").current()
    if stage not in ("request", "decompose"):
        raise DiscussionError(f"discussion belongs to Decompose; this case is at {stage!r}")


def discuss(case: Path, client=None, model=None, specialists=None, allow_single=False) -> dict:
    case = Path(case)
    if (case / "request.md").exists():
        _at_decompose(case)
    request = (case / "request.md").read_text(encoding="utf-8").strip()
    if not request:
        raise DiscussionError("request.md is empty")
    reg = registry.Registry()
    people = tuple(specialists or reg.specialists)
    if len(people) < 2 and not (allow_single and people):
        raise DiscussionError("at least two independent perspectives required (or allow_single=True)")
    ids = reg.design_ids()
    snapshot = actions.fingerprint(request)
    if client is None:
        from openai import OpenAI
        client = OpenAI(base_url=chat.settings()["base_url"],
                        api_key=os.environ.get("WRF_AGENT_LLM_API_KEY") or "none")
    model = model or chat.settings()["model"]
    with writer(case):
        actions.invalidate_approval(case, "new discussion")
        proposals, critiques, failures, raws = {}, {}, {}, {}
        def stage(name, system, payload, check):
            raws[name] = []
            return _answer(client, model, system, payload, check=check, raw=raws[name])

        try:
            for person in people:
                try:
                    proposal = _answer(client, model,
                        f"Independent WRF {person.title} perspective. You have not seen any other expert's answer. "
                        "Answer with ONE JSON object and nothing else, shaped exactly like:\n"
                        '{"design": {"<field_id>": {"value": "<string>", "reason": "<one sentence>"}}, '
                        '"assumptions": ["..."], "risks": ["..."], "open_questions": ["..."]}\n'
                        "Every id in design_ids must appear exactly once in design, with a non-empty string value "
                        "and a reason; per-domain fields take a comma-separated string such as '1,0,0'. "
                        "Fields listed in same_all_domains take ONE value that applies to every domain — never a "
                        "per-domain list (wrf.exe would rewrite them). "
                        "Do not invent data availability; state uncertainty in assumptions or risks.",
                        {"request": request, "perspective": person.summary, "design_ids": ids,
                         "same_all_domains": [k for k, c in checks.constraints().items()
                                              if c["kind"] == "same_all_domains"]},
                        check=lambda r: (_design(r.get("design"), ids),
                                         [_list(r, k) for k in ("assumptions", "risks", "open_questions")]),
                        raw=raws.setdefault(f"proposal:{person.id}", []))
                except Exception as exc:
                    failures[person.id] = {"error": f"{type(exc).__name__}: {exc}"}
                    raise
                proposals[person.id] = proposal
            if len(people) == 1:
                # single-perspective arm: no cross-review; the proposal itself is the only alternative (deterministic)
                synthesis = {"alternatives": [{"id": "A", "design": proposals[people[0].id]["design"],
                                               "tradeoffs": ["single-perspective arm: no alternatives generated"]}],
                            "disagreements": [],
                            "open_questions": proposals[people[0].id].get("open_questions", [])}
            else:
                for person in people:
                    critique = stage(
                        f"critique:{person.id}",
                        f"As {person.title}, cross-examine the other proposals on this request. Answer with ONE JSON "
                        'object shaped exactly like {"agreements": ["..."], "disagreements": ["..."], '
                        '"open_questions": ["..."]}. Name the field and the position taken when you disagree; '
                        "do not manufacture consensus and do not agree for politeness.",
                        {"request": request, "perspective": person.summary, "proposals": proposals},
                        check=lambda r: [_list(r, k) for k in ("agreements", "disagreements", "open_questions")])
                    critiques[person.id] = critique
            if len(people) > 1:
                def check_synthesis(r):
                    alts = r.get("alternatives")
                    if not isinstance(alts, list) or not alts:
                        raise DiscussionError("alternatives must be a non-empty list")
                    if len({a.get("id") for a in alts}) != len(alts):
                        raise DiscussionError("alternative ids must be unique")
                    for a in alts:
                        if not isinstance(a, dict):
                            raise DiscussionError("every alternative must be an object")
                        if not a.get("id"):
                            raise DiscussionError("every alternative needs an id")
                        _design(a.get("design"), ids, require_all=True)
                        _list(a, "tradeoffs")
                    for key in ("disagreements", "open_questions"):
                        _list(r, key)

                synthesis = stage(
                    "synthesis",
                    "Synthesize the proposals without forcing consensus. Answer with ONE JSON object shaped exactly "
                    'like {"alternatives": [{"id": "A", "design": {"<field_id>": {"value": "<string>", '
                    '"reason": "<one sentence>"}}, "tradeoffs": ["..."]}], "disagreements": ["..."], '
                    '"open_questions": ["..."]}. Keep each real disagreement as a disagreement; every alternative '
                    "design must contain every field id in design_ids, exactly once, with a non-empty value and a "
                    "reason. Two alternatives must differ in at least one design value.",
                    {"request": request, "proposals": proposals, "critiques": critiques, "design_ids": ids},
                    check=check_synthesis)
            record = {"status": "complete", "snapshot_hash": snapshot, "request": request,
                      "model": model, "proposals": proposals, "critiques": critiques, "synthesis": synthesis,
                      "raws": raws}
        except Exception as exc:
            record = {"status": "incomplete", "snapshot_hash": snapshot, "request": request,
                      "model": model, "proposals": proposals, "critiques": critiques, "failures": failures,
                      "raws": raws, "error": f"{type(exc).__name__}: {exc}"}
        (case / "discussion.yaml").write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False), encoding="utf-8")
        Journal(case, "request").note("discussion", f"discussion {record['status']} for {snapshot}")
        return record


def select(case: Path, alternative: str) -> dict:
    """Human choice inside Decompose, not approval; Review Submit is still mandatory."""
    case = Path(case)
    _at_decompose(case)
    with writer(case):
        p = case / "discussion.yaml"
        record = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        request = (case / "request.md").read_text(encoding="utf-8").strip()
        if record.get("status") != "complete" or record.get("snapshot_hash") != actions.fingerprint(request):
            raise DiscussionError("discussion incomplete or request changed")
        matches = [a for a in record["synthesis"]["alternatives"] if a["id"] == alternative]
        if len(matches) != 1:
            raise DiscussionError("alternative not found")
        draft = {"wrf_version": "4.6.1", "discussion_hash": record["snapshot_hash"],
                 "selected_alternative": alternative, "design": matches[0]["design"]}
        (case / "draft.yaml").write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")
        actions.invalidate_approval(case, "human selected a discussion alternative; Submit required")
        Journal(case, "request").note("user", f"selected alternative {alternative} for {record['snapshot_hash'][:12]}")
        return draft
