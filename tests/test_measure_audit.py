"""The audit numbers are derived, never typed: definitions frozen in YAML, counts recomputed from the ledgers."""

import json
from pathlib import Path

import pytest

from src.userguide import check_a_mundo, measure_audit

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "references" / "upstream" / "wrf-4.6.1"
SRC_461 = UP / "check_a_mundo_versions" / "sources" / "module_check_a_mundo_v4.6.1.F"


@pytest.mark.skipif(not (UP / "check_a_mundo.json").exists(), reason="ledger not present")
def test_counts_are_consistent_and_definitions_match_field_map():
    r = measure_audit.measure()
    a = r["audit"]
    for kind in ("refusals", "rewrites"):
        k = a[kind]
        assert k["printed_at_default_debug_level"] + k["printed_only_with_debug"] + k["not_printed"] == k["total"]
        assert k["involve_study_defining"] <= k["total"]
    assert a["rewrites"]["replace_study_defining"] <= a["rewrites"]["involve_study_defining"]
    # the frozen study-defining set is exactly field_map's design class
    assert r["definitions"]["study_defining_drift"] == {"only_in_definitions": [], "only_in_field_map": []}
    # every line a rule cites is an audited statement
    assert r["coverage"]["cited_lines_not_in_ledger"] == []
    # the ledger carries no local path: the source is a public release tag
    assert r["audit"]["source"].startswith("wrf-model/WRF@v")


@pytest.mark.skipif(not SRC_461.exists(), reason="pinned WRF source not present")
def test_ledger_is_reproducible_from_the_pinned_source(tmp_path):
    led = json.loads((UP / "check_a_mundo.json").read_text())
    again = check_a_mundo.extract(SRC_461, led["source"], led["wrf_version"])
    assert again["source_sha256"] == led["source_sha256"]
    assert again["items"] == led["items"]


def test_single_line_if_and_body_statements_get_no_borrowed_print(tmp_path):
    src = tmp_path / "cam.F"
    src.write_text(
        "SUBROUTINE t\n"
        "  CALL wrf_message ( 'body' )\n"
        "  model_config_rec % a = 1\n"
        "  IF ( model_config_rec % b == 1 ) model_config_rec % b = 2\n"
        "  IF ( model_config_rec % c == 1 ) THEN\n"
        "    model_config_rec % c = 2\n"
        "    CALL wrf_debug ( 1, 'note' )\n"
        "  ELSE\n"
        "    wrf_err_message = 'refused'\n"
        "    CALL wrf_message ( wrf_err_message )\n"
        "    count_fatal_error = count_fatal_error + 1\n"
        "  END IF\n"
        "END SUBROUTINE t\n")
    items = check_a_mundo.extract(src)["items"]
    level = {(i["kind"], i["target"]): i["emit_level"] for i in items}
    assert level[("rewrite", "a")] is None        # body statement
    assert level[("rewrite", "b")] is None        # single-line IF
    assert level[("rewrite", "c")] == 1           # printed only with debug_level >= 1
    assert level[("refuse", None)] == 0           # printed at the default level
