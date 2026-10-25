"""Case-directory fixtures for the flow tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.flow_fixtures import draft_doc, spec_doc


@pytest.fixture()
def case(tmp_path: Path) -> Path:
    d = tmp_path / "case"
    d.mkdir()
    return d


@pytest.fixture()
def draft_yaml() -> dict:
    return draft_doc()


@pytest.fixture()
def spec_yaml() -> dict:
    return spec_doc()
