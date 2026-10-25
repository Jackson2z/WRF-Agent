#!/usr/bin/env python3
"""measure_audit — every number the paper reports about WRF's start-up checks, from one place.

Inputs (all in the repository):
  references/upstream/wrf-4.6.1/check_a_mundo.json                       ledger of the evaluated release
  references/upstream/wrf-4.6.1/check_a_mundo_versions/*.json            ledgers of neighbouring releases
  references/upstream/wrf-4.6.1/builds/<default_build>/                  the recorded build (accepted variables)
  docs/measurements/audit_definitions.yaml                               definitions, frozen before counting

Output: docs/measurements/audit_numbers.json (+ .md). No number in the paper is typed by hand.

    python3 -m src.userguide.measure_audit [--out docs/measurements]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from . import checks

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UP = ROOT / "references" / "upstream" / "wrf-4.6.1"
DEFS = ROOT / "docs" / "measurements" / "audit_definitions.yaml"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _design_fields() -> set[str]:
    fm = yaml.safe_load((HERE / "field_map.yaml").read_text(encoding="utf-8"))["fields"]
    return {k.split(":", 1)[1] for k, v in fm.items() if v.get("class") == "design"}


def ported_lines() -> set[int]:
    """Source lines of module_check_a_mundo.F that a rule in the constraint set cites."""
    lines = {int(c["source"].rsplit(":", 1)[1]) for c in checks.constraints().values()
             if str(c.get("source", "")).rsplit(":", 1)[-1].isdigit()}
    lines |= {v[0] for v in checks.CAM_BUILD_DEPENDENT.values()}
    text = (HERE / "checks.py").read_text(encoding="utf-8")
    lines |= {int(n) for n in re.findall(r"\{CAM\}:(\d+)", text)}
    return lines


def _sig(i: dict) -> tuple:
    return (i["kind"], i["subroutine"], i["target"], i["value"], tuple(i["conditions"]))


def _split(items: list[dict]):
    return [i for i in items if i["kind"] == "refuse"], [i for i in items if i["kind"] == "rewrite"]


def measure() -> dict:
    defs = yaml.safe_load(DEFS.read_text(encoding="utf-8"))
    S = set(defs["study_defining"]["params"])
    fm_design = _design_fields()
    s_drift = {"only_in_definitions": sorted(S - fm_design), "only_in_field_map": sorted(fm_design - S)}

    led_path = UP / "check_a_mundo.json"
    led = json.loads(led_path.read_text())
    ref, rw = _split(led["items"])
    involves = lambda i: bool(set(i["params"]) & S)

    def announced(its):
        return {
            "printed_at_default_debug_level": sum(1 for i in its if i.get("emit_level") == 0),
            "printed_only_with_debug": sum(1 for i in its if (i.get("emit_level") or 0) >= 1),
            "not_printed": sum(1 for i in its if i.get("emit_level") is None),
            "printed_at_default_upper_bound": sum(1 for i in its if i.get("emit_level") == 0
                                                  or i.get("nearby_emit_level") == 0),
            "message_text_present": sum(1 for i in its if i["message"]),
        }

    ported = ported_lines()
    led_lines = {i["line"] for i in led["items"]}

    rw_inv = [i for i in rw if involves(i)]
    rw_rep = [i for i in rw if i["target"] in S]
    ref_inv = [i for i in ref if involves(i)]
    audit = {
        "release": led.get("wrf_version"), "source": led["source"], "source_sha256": led.get("source_sha256"),
        "refusals": {"total": len(ref), **announced(ref),
                     "involve_study_defining": len(ref_inv),
                     "involve_study_defining_printed_at_default": sum(1 for i in ref_inv if i.get("emit_level") == 0)},
        "rewrites": {"total": len(rw), **announced(rw),
                     "distinct_targets": len({i["target"] for i in rw}),
                     "involve_study_defining": len(rw_inv),
                     "involve_study_defining_distinct_targets": len({i["target"] for i in rw_inv}),
                     "involve_study_defining_printed_at_default": sum(1 for i in rw_inv if i.get("emit_level") == 0),
                     "replace_study_defining": len(rw_rep),
                     "replace_study_defining_distinct_targets": sorted({i["target"] for i in rw_rep}),
                     "replace_study_defining_printed_at_default": sum(1 for i in rw_rep if i.get("emit_level") == 0),
                     "replace_study_defining_printed_at_default_upper_bound": sum(
                         1 for i in rw_rep if i.get("emit_level") == 0 or i.get("nearby_emit_level") == 0)},
    }

    # rewritable fraction of one recorded build
    bdir = checks.build_dir()
    accepted = checks.build_accepts() or set()
    targets = {i["target"] for i in rw}
    build = {
        "label": bdir.name if bdir else None,
        "accepted_variables": len(accepted),
        "rewrite_targets_accepted": len(targets & accepted),
        "rewrite_targets_not_in_build": len(targets - accepted),
        "fraction": round(len(targets & accepted) / len(accepted), 4) if accepted else None,
    }

    # drift across releases
    vdir = UP / "check_a_mundo_versions"
    ledgers = {"4.6.1": led}
    for p in sorted(vdir.glob("check_a_mundo_v*.json")):
        d = json.loads(p.read_text())
        ledgers[d.get("wrf_version") or p.stem.split("_v", 1)[1]] = d
    order = [v for v in defs["drift"]["releases"] if v in ledgers]
    series = []
    for v in order:
        r, w = _split(ledgers[v]["items"])
        series.append({"release": v, "refusals": len(r), "rewrites": len(w),
                       "source": ledgers[v]["source"], "source_sha256": ledgers[v].get("source_sha256")})
    steps = []
    for a, b in zip(order, order[1:]):
        sa, sb = {_sig(i) for i in ledgers[a]["items"]}, {_sig(i) for i in ledgers[b]["items"]}
        added, removed = sb - sa, sa - sb
        steps.append({"from": a, "to": b,
                      "added": {"refuse": sum(1 for s in added if s[0] == "refuse"),
                                "rewrite": sum(1 for s in added if s[0] == "rewrite")},
                      "removed": {"refuse": sum(1 for s in removed if s[0] == "refuse"),
                                  "rewrite": sum(1 for s in removed if s[0] == "rewrite")}})

    # which audited statements the constraint set encodes (T5)
    cov = {
        "cited_lines": len(ported),
        "cited_lines_not_in_ledger": sorted(ported - led_lines),
        "refusals_encoded": sum(1 for i in ref if i["line"] in ported),
        "rewrites_encoded": sum(1 for i in rw if i["line"] in ported),
        "study_defining_refusals_encoded": sum(1 for i in ref_inv if i["line"] in ported),
        "study_defining_rewrites_encoded": sum(1 for i in rw_inv if i["line"] in ported),
        "study_defining_replacements_encoded": sum(1 for i in rw_rep if i["line"] in ported),
    }

    return {
        "definitions": {"file": "docs/measurements/audit_definitions.yaml", "sha256": _sha(DEFS),
                        "status": defs.get("status"), "study_defining_size": len(S),
                        "study_defining_drift": s_drift},
        "inputs": {"ledger": {"file": str(led_path.relative_to(ROOT)), "sha256": _sha(led_path)}},
        "audit": audit, "build": build, "drift": {"series": series, "steps": steps}, "coverage": cov,
        "generated_by": "src/userguide/measure_audit.py",
    }


def _md(r: dict) -> str:
    a, b, c = r["audit"], r["build"], r["coverage"]
    L = ["# Start-up check audit — measured numbers", "",
         f"Release {a['release']} (`{a['source']}`, sha256 `{a['source_sha256'][:12]}…`); "
         f"definitions `{r['definitions']['file']}` ({r['definitions']['status']}).", "",
         "| quantity | refusals | rewrites |", "|---|---|---|"]
    for k in ("total", "printed_at_default_debug_level", "printed_at_default_upper_bound", "printed_only_with_debug",
              "not_printed", "message_text_present", "involve_study_defining", "involve_study_defining_printed_at_default"):
        L.append(f"| {k} | {a['refusals'].get(k, '—')} | {a['rewrites'].get(k, '—')} |")
    L += ["", f"Rewrites that replace a study-defining value: {a['rewrites']['replace_study_defining']} statements on "
          f"{len(a['rewrites']['replace_study_defining_distinct_targets'])} parameters "
          f"({', '.join(a['rewrites']['replace_study_defining_distinct_targets'])}); "
          f"{a['rewrites']['replace_study_defining_printed_at_default']} printed at the default debug level.", "",
          f"Build `{b['label']}`: {b['rewrite_targets_accepted']} of {b['accepted_variables']} accepted namelist "
          f"variables are rewrite targets ({b['fraction']:.1%}).", "", "| release | refusals | rewrites |",
          "|---|---|---|"]
    L += [f"| {s['release']} | {s['refusals']} | {s['rewrites']} |" for s in r["drift"]["series"]]
    L += ["", "| step | +refuse | −refuse | +rewrite | −rewrite |", "|---|---|---|---|---|"]
    L += [f"| {s['from']}→{s['to']} | {s['added']['refuse']} | {s['removed']['refuse']} | {s['added']['rewrite']} | "
          f"{s['removed']['rewrite']} |" for s in r["drift"]["steps"]]
    L += ["", f"Constraint set encodes {c['refusals_encoded']} refusals and {c['rewrites_encoded']} rewrites "
          f"({c['study_defining_refusals_encoded']} / {c['study_defining_rewrites_encoded']} of the study-defining ones).",
          ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "measurements")
    a = ap.parse_args(argv)
    r = measure()
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "audit_numbers.json").write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
    (a.out / "audit_numbers.md").write_text(_md(r), encoding="utf-8")
    print(json.dumps({"refusals": r["audit"]["refusals"]["total"], "rewrites": r["audit"]["rewrites"]["total"],
                      "build": r["build"], "drift": r["drift"]["series"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
