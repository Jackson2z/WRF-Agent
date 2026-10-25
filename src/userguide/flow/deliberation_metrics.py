"""deliberation_metrics — what the deliberation actually produced, as numbers read off the record.

Everything here is computed from the files discuss()/select()/approve wrote (discussion.yaml, draft.yaml,
experiment.yaml); no model call, no judgement. The paper's three known weaknesses in the deliberation layer
(how much initial divergence there was, whether dissent survived to the synthesis, whether a person engaged with
the alternatives) each get one machine-checkable number.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _y(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def _val(entry):
    return str((entry or {}).get("value")).strip() if isinstance(entry, dict) else str(entry).strip()


def metrics(case: Path) -> dict:
    """Deliberation metrics for one case directory (safe on partial records: absent parts are reported as None)."""
    case = Path(case)
    d = _y(case / "discussion.yaml") or {}
    draft = _y(case / "draft.yaml") or {}
    experiment = _y(case / "experiment.yaml") or {}
    out: dict = {"status": d.get("status"), "model": d.get("model")}

    proposals = d.get("proposals") or {}
    out["specialists"] = len(proposals)
    per = {}
    for sid, p in proposals.items():
        per[sid] = {"assumptions": len(p.get("assumptions") or []),
                    "risks": len(p.get("risks") or []),
                    "open_questions": len(p.get("open_questions") or [])}
    out["per_proposal"] = per

    # initial divergence: fields on which the independent proposals disagree
    fields: dict[str, set] = {}
    for p in proposals.values():
        for fid, entry in (p.get("design") or {}).items():
            fields.setdefault(fid, set()).add(_val(entry))
    divergent = sorted(f for f, vs in fields.items() if len(vs) > 1)
    out["design_fields"] = len(fields)
    out["initial_divergence"] = {"fields": len(divergent), "list": divergent,
                                 "rate": round(len(divergent) / len(fields), 4) if fields else None}

    critiques = d.get("critiques") or {}
    out["cross_review"] = {"reviewers": len(critiques),
                           "disagreements": sum(len(c.get("disagreements") or []) for c in critiques.values()),
                           "open_questions": sum(len(c.get("open_questions") or []) for c in critiques.values())}

    syn = d.get("synthesis") or {}
    alts = syn.get("alternatives") or []
    alt_fields = sorted({f for a in alts for f in (a.get("design") or {})})
    differing = []
    for f in alt_fields:
        if len({_val(a["design"].get(f)) for a in alts if f in a["design"]}) > 1:
            differing.append(f)
    out["synthesis"] = {"alternatives": len(alts), "fields_differing_across_alternatives": differing,
                        "kept_disagreements": len(syn.get("disagreements") or []),
                        "open_questions": len(syn.get("open_questions") or [])}

    # human engagement: which alternative was selected, and which values the person changed in review
    sel = draft.get("selected_alternative")
    chosen = next((a for a in alts if a.get("id") == sel), None)
    edited = [fid for fid, entry in (draft.get("design") or {}).items()
              if isinstance(entry, dict) and entry.get("edited_by") == "user"]
    votes: dict[str, int] = {}
    if chosen:
        for fid, entry in (chosen.get("design") or {}).items():
            votes[fid] = sum(1 for p in proposals.values()
                             if _val((p.get("design") or {}).get(fid)) == _val(entry))
    out["selection"] = {"alternative": sel,
                        "human_edited_fields": len(edited), "human_edited_list": edited,
                        "fields_supported_by_all": sum(1 for f in votes if votes[f] == len(proposals)),
                        "fields_supported_by_minority": sum(1 for f in votes if proposals and votes[f] < len(proposals) / 2)}

    # dissent survival: a kept disagreement that names a field the person then decided (selected or edited)
    kept_on_decided = 0
    decided = set(draft.get("design") or {})
    for text in syn.get("disagreements") or []:
        if any(f in str(text) for f in decided):
            kept_on_decided += 1
    out["dissent"] = {"kept_in_synthesis": out["synthesis"]["kept_disagreements"],
                      "naming_a_decided_field": kept_on_decided}

    # cost: calls, repair rounds, tokens, wall time — from the raw transcripts
    raws = d.get("raws") or {}
    calls = repairs = prompt = completion = 0
    elapsed = 0.0
    for entries in raws.values():
        for e in entries:
            calls += 1
            repairs += 1 if e.get("attempt", 0) > 0 else 0
            elapsed += e.get("elapsed_s") or 0
            t = e.get("tokens") or {}
            prompt += t.get("prompt_tokens") or 0
            completion += t.get("completion_tokens") or 0
    out["cost"] = {"llm_calls": calls, "repair_rounds": repairs, "prompt_tokens": prompt,
                   "completion_tokens": completion, "llm_wall_s": round(elapsed, 1),
                   "tokens_recorded": bool(prompt or completion)}

    # outcome: what the compiled specification did
    if experiment:
        out["outcome"] = {"approved": bool(experiment.get("approved_by")),
                          "changed_from_draft": len(experiment.get("changed_from_draft") or [])}
    return out
