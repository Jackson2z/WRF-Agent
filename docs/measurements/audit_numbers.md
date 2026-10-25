# Start-up check audit — measured numbers

Release 4.6.1 (`wrf-model/WRF@v4.6.1:share/module_check_a_mundo.F`, sha256 `2d6d4eb1f918…`); definitions `docs/measurements/audit_definitions.yaml` (frozen).

| quantity | refusals | rewrites |
|---|---|---|
| total | 97 | 278 |
| printed_at_default_debug_level | 95 | 21 |
| printed_at_default_upper_bound | 97 | 76 |
| printed_only_with_debug | 0 | 73 |
| not_printed | 2 | 184 |
| message_text_present | 89 | 47 |
| involve_study_defining | 41 | 115 |
| involve_study_defining_printed_at_default | 41 | 19 |

Rewrites that replace a study-defining value: 21 statements on 9 parameters (bl_pbl_physics, cu_physics, dx, mp_physics, ra_lw_physics, ra_sw_physics, sf_sfclay_physics, sf_surface_physics, sf_urban_physics); 4 printed at the default debug level.

Build `wrf461-dmpar`: 132 of 2166 accepted namelist variables are rewrite targets (6.1%).

| release | refusals | rewrites |
|---|---|---|
| 4.4.2 | 95 | 246 |
| 4.5.1 | 95 | 246 |
| 4.6.1 | 97 | 278 |
| 4.7.1 | 97 | 281 |

| step | +refuse | −refuse | +rewrite | −rewrite |
|---|---|---|---|---|
| 4.4.2→4.5.1 | 2 | 2 | 6 | 6 |
| 4.5.1→4.6.1 | 2 | 0 | 33 | 1 |
| 4.6.1→4.7.1 | 2 | 2 | 17 | 14 |

Constraint set encodes 13 refusals and 3 rewrites (13 / 2 of the study-defining ones).
