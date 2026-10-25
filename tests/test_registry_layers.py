"""The parameter registry is generated from named files, and every field says which layer it belongs to.

Users' Guide parameters are the base layer; anything else is core Registry, an extension package's Registry file,
or accepted only by a specific compiled wrf.exe (build profile). Changing an input file and regenerating is the
only way the registry changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.userguide import parse

SNAP = Path(__file__).resolve().parents[1] / "references" / "upstream" / "wrf-4.6.1"
needs_snapshot = pytest.mark.skipif(not (SNAP / "Registry" / "Registry.EM").exists(), reason="snapshot absent")


@needs_snapshot
def test_every_field_has_a_layer_and_a_source():
    data = parse.build(SNAP)
    assert data["generated_from"] and data["regenerate"].startswith("python3 -m src.userguide parse")
    for f in data["fields"]:
        assert f["layer"] in {"base", "core", "extension", "build", "not-in-build"}, f["name"]
        assert f["provided_by"], f["name"]
    documented = [f for f in data["fields"] if f["readme"]]
    assert documented and all(f["layer"] == "base" for f in documented)   # floor = README.namelist in the source


@needs_snapshot
def test_extension_layer_names_its_registry_file():
    data = parse.build(SNAP)
    fire = [f for f in data["fields"] if f["layer"] == "extension" and f["provided_by"] == "registry.fire"]
    assert fire, "fire package variables should be attributed to registry.fire"


@needs_snapshot
def test_registry_entry_changes_the_universe():
    em = parse.parse_registry(SNAP / "Registry", "Registry.EM")
    chem = parse.parse_registry(SNAP / "Registry", "Registry.EM_CHEM")
    assert set(em) < set(chem)            # WRF-Chem adds variables, removes none


BUILD = SNAP / "builds" / "wrf461-dmpar"


@pytest.mark.skipif(not (BUILD / "build_profile.yaml").exists(), reason="no build profile")
def test_build_profile_adds_compiled_variables_and_checks_the_floor():
    nb = parse.build(SNAP)
    wb = parse.build(SNAP, BUILD)
    assert wb["stats"]["namelist_input_fields"] > nb["stats"]["namelist_input_fields"]
    assert any(f["layer"] == "build" for f in wb["fields"])
    assert wb["stats"]["readme_missing_from_build"] == []     # everything the source documents, this build accepts
    assert "ug_missing_from_build" in wb["stats"]            # the online Guide may list newer names
    assert wb["build"] == "wrf461-dmpar" and any(i.get("build") == "wrf461-dmpar" for i in wb["generated_from"])
