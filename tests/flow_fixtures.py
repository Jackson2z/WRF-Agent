"""Shared fixtures for the flow tests: a design that passes every compile rule, and YAML writers."""

from __future__ import annotations

from pathlib import Path

import yaml

PASSING_DESIGN = {
    "wps:start_date": "2024-01-01_00:00:00", "wps:end_date": "2024-01-02_00:00:00",
    "input:restart_interval": "1440", "wps:max_dom": "1", "wps:ref_lat": "1.35",
    "wps:ref_lon": "103.8", "wps:map_proj": "lambert", "wps:dx": "3000",
    "wps:parent_grid_ratio": "3", "wps:e_we": "100", "wps:e_sn": "100",
    "wps:i_parent_start": "1", "wps:j_parent_start": "1", "input:e_vert": "45",
    "input:mp_physics": "6", "input:bl_pbl_physics": "2", "input:sf_sfclay_physics": "2",
    "input:sf_surface_physics": "2", "input:ra_lw_physics": "4", "input:ra_sw_physics": "4",
    "input:cu_physics": "0", "input:sf_urban_physics": "0", "input:use_wudapt_lcz": "0",
    "input:history_interval": "60", "input:frames_per_outfile": "24", "wps:stand_lon": "103.8",
    "wps:truelat1": "1.35", "wps:truelat2": "1.35",
}


def draft_doc() -> dict:
    return {"wrf_version": "4.6.1",
            "design": {k: {"value": v, "reason": f"test reason for {k}"} for k, v in PASSING_DESIGN.items()}}


def spec_doc() -> dict:
    return {"wrf_version": "4.6.1", "approved_by": "user", "approved_at": "2026-10-24T00:00:00+00:00",
            "design": {k: {"value": v, "reason": "test"} for k, v in PASSING_DESIGN.items()},
            "changed_from_draft": [], "agent_reasons": {}}


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
