#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URBPARM.TBL scenario modifier — generic WRF urban parameter table editor.

Three usage modes:
  1. Preset:      modify_urbparm(content, preset="bipv")
  2. Custom:      modify_urbparm(content, albr=0.12, ah_delta=15, ah_option=1)
  3. YAML batch:  modify_urbparm_batch(content, scenarios_dict)

Library import:
  from WRF_Simulation.urbparm_modifier import apply_scenario, PRESETS

CLI:
  wrf-kit modify-urbparm --input URBPARM.TBL --preset bipv
  wrf-kit modify-urbparm --input URBPARM.TBL --config scenarios.yaml
"""

import re

# ──────────────────── presets ────────────────────

PRESETS = {
    # Main scenarios (main-5)
    "ctl": {"albr": None, "albb": None, "ah_delta": 0, "ah_option": None},
    "bipv": {"albr": 0.12, "albb": None, "ah_delta": 15, "ah_option": 1},
    "fpv": {"albr": None, "albb": 0.12, "ah_delta": 15, "ah_option": 1},
    "coolroof": {"albr": 0.60, "albb": None, "ah_delta": 0, "ah_option": None},
    "bipv+cr": {"albr": 0.60, "albb": 0.12, "ah_delta": 15, "ah_option": 1},
    # DOE corners (pure albedo perturbation)
    "a12_w12": {"albr": 0.12, "albb": 0.12, "ah_delta": 0, "ah_option": None},
    "a12_w60": {"albr": 0.12, "albb": 0.60, "ah_delta": 0, "ah_option": None},
    "a20_w60": {"albr": 0.20, "albb": 0.60, "ah_delta": 0, "ah_option": None},
    "a60_w60": {"albr": 0.60, "albb": 0.60, "ah_delta": 0, "ah_option": None},
}

# ──────────────────── core functions ────────────────────


def set_3class(content: str, key: str, value: float) -> str:
    """Set `KEY: v1, v2, v3` (3-class USGS) all to *value*."""
    pattern = rf"({key}:\s+)[0-9.]+,\s*[0-9.]+,\s*[0-9.]+"
    repl = rf"\g<1>{value}, {value}, {value}"
    new, n = re.subn(pattern, repl, content)
    if n == 0:
        raise ValueError(f"Pattern {key}: not found (verify 3-class USGS baseline)")
    return new


def shift_ah(content: str, delta: float) -> str:
    """Add *delta* W/m² to each AH class."""
    if delta == 0:
        return content

    def _shift(m):
        vals = [float(x.strip()) + delta for x in m.group(1).split(",")]
        return "AH:  " + ", ".join(f"{v:.1f}" for v in vals)

    new, n = re.subn(r"^AH:\s+([0-9., ]+)", _shift, content, flags=re.MULTILINE)
    if n == 0:
        raise ValueError("Pattern AH: not found")
    return new


def set_option(content: str, key: str, value: int) -> str:
    """Set single-value parameter (AHOPTION etc.)."""
    pattern = rf"({key}:\s*)\d+"
    repl = rf"\g<1>{value}"
    new, n = re.subn(pattern, repl, content)
    if n == 0:
        raise ValueError(f"Pattern {key}: not found")
    return new


def apply_scenario(content: str, params: dict) -> str:
    """Apply scenario parameters to URBPARM content. None values are skipped."""
    if params.get("albr") is not None:
        content = set_3class(content, "ALBR", params["albr"])
    if params.get("albb") is not None:
        content = set_3class(content, "ALBB", params["albb"])
    if params.get("ah_delta", 0) != 0:
        content = shift_ah(content, params["ah_delta"])
    if params.get("ah_option") is not None:
        content = set_option(content, "AHOPTION", params["ah_option"])
    return content


def verify_3class(content: str) -> int:
    """Verify baseline is 3-class USGS. Returns category count."""
    m = re.search(r"Number of urban categories:\s*(\d+)", content)
    ncat = int(m.group(1)) if m else 0
    if ncat != 3:
        raise ValueError(
            f"Baseline URBPARM is not 3-class USGS (got {ncat or 'NA'}). "
            f"Must use URBPARM_original.TBL (3-class)."
        )
    return ncat


def print_key_lines(content: str, keys=("ALBR", "ALBB", "AH", "AHOPTION")):
    """Print key parameter lines for quick verification."""
    for ln in content.splitlines():
        if any(ln.startswith(f"{k}:") for k in keys):
            print(f"  {ln.strip()}")
