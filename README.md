# WRF-Agent — public preview (v0.1.0-preview)

> **Status: testing stage.** This repository is a reduced, read-only preview of a larger code base that is still
> being built. It contains the parts needed to reproduce the configuration-fidelity measurements: the start-up
> check audit of WRF, the specification checks, and deterministic namelist generation. Interfaces may change.

## What is in this preview

| Part | Where | What it does |
|---|---|---|
| Start-up check audit | `src/userguide/check_a_mundo.py` | Extracts every refusal and every value rewrite from WRF's `share/module_check_a_mundo.F`, with the enclosing conditions and the level at which each one is printed |
| Audit numbers | `src/userguide/measure_audit.py` | Counts the extracted behaviours under the definitions frozen in `docs/measurements/audit_definitions.yaml` |
| Specification checks | `src/userguide/checks.py`, `field_map.yaml` | Rule checks on an experiment specification (nesting, per-domain consistency, scheme compatibility, build-dependent options) |
| Generation | `src/userguide/emit.py` | Deterministic emission of `namelist.wps` / `namelist.input` with a per-parameter record of where each value came from |
| Batch expansion | `src/userguide/batch.py` | Expands a design matrix into member specifications that differ only on declared factors |
| Measurements | `src/userguide/measure_fidelity.py` | Fault injection over the rule set, approval binding under edits, determinism and provenance of generation |
| Pinned upstream | `references/upstream/wrf-4.6.1/` | WRF Registry, `README.namelist`, start-up check sources for 4.4.2 / 4.5.1 / 4.6.1 / 4.7.1 (from the `wrf-model/WRF` release tags) and one recorded build profile |

Not in this preview: job submission and monitoring, the interactive studio, the agent interfaces.
No step in this repository runs WRF or contacts a computing host.

## Reproduce the numbers

```bash
python -m pip install -e ".[test]"                     # Python >= 3.10
python -m src.userguide parse --build wrf461-dmpar      # parameter registry from the pinned snapshot (offline)
python -m src.userguide.measure_audit                   # -> docs/measurements/audit_numbers.{json,md}
python -m src.userguide.measure_fidelity                # -> docs/measurements/fidelity_measurements.{json,md}
python -m pytest -q
```

To audit another release, download its `share/module_check_a_mundo.F` from the corresponding `wrf-model/WRF` tag and run

```bash
python -m src.userguide.check_a_mundo --src module_check_a_mundo.F --out check_a_mundo.json \
    --label wrf-model/WRF@vX.Y.Z:share/module_check_a_mundo.F --version X.Y.Z
```

## Licence

MIT (this code). WRF source files under `references/upstream/` are reproduced from the public `wrf-model/WRF`
repository under its own terms.
