# Constraint enforcement and approval binding — measured

- Rule inventory (read from checks.py): **27** rule ids — 19 enforcing, 5 informational, 3 deferred
- Enforcing rules with a positive and a negative (or measured-unreachable) test: **19/19**
- Declared domain constraints negative-tested: 8/8
- Valid specification: verdict **PASS**, 18 rule kinds evaluated
- Fault injection: **29/29** faults detected as expected; 11 of them raise no other rule
- cfl_timestep over 8 dx values: ['PASS']

| rule | class | statuses | positive | negative |
|---|---|---|---|---|
| `cam_build_dependent` | enforcing | DEFERRED/FAIL/PASS | yes (explicit positive test) | yes |
| `cam_full_sbm` | enforcing | FAIL | yes (not raised on the valid specification) | yes |
| `cfl_timestep` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | unreachable |
| `date_format` | enforcing | FAIL | yes (not raised on the valid specification) | yes |
| `domain_constraint` | enforcing | FAIL | yes (not raised on the valid specification) | yes |
| `domain_min_100` | enforcing | PASS/WARN | yes (evaluated on the valid specification) | yes |
| `dx_chain` | informational | PASS | yes (evaluated on the valid specification) | — |
| `evert_min` | enforcing | PASS/WARN | yes (evaluated on the valid specification) | yes |
| `evert_same` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | yes |
| `lagrange_coarse_input` | deferred | DEFERRED | yes (evaluated on the valid specification) | — |
| `land_cat_match` | deferred | DEFERRED | yes (evaluated on the valid specification) | — |
| `nest_divisible` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | yes |
| `nest_inside_parent` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | yes |
| `pbl_sfclay_compat` | enforcing | FAIL/PASS/WARN | yes (evaluated on the valid specification) | yes |
| `per_domain_length` | enforcing | FAIL | yes (not raised on the valid specification) | yes |
| `period_positive` | enforcing | FAIL | yes (not raised on the valid specification) | yes |
| `ratio_odd` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | yes |
| `required` | enforcing | FAIL | yes (not raised on the valid specification) | yes |
| `rrtmg_o3input` | informational | PASS | yes (evaluated on the valid specification) | — |
| `urban_bep_pbl` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | yes |
| `urban_noah` | enforcing | FAIL/PASS | yes (evaluated on the valid specification) | yes |
| `urbparm_effective` | deferred | DEFERRED | yes (evaluated on the valid specification) | — |
| `vertical_grid_distinct` | informational | PASS | yes (evaluated on the valid specification) | — |
| `wps_wrf_same_source` | informational | PASS | yes (evaluated on the valid specification) | — |
| `wrf_startup_checks` | informational | PASS | yes (evaluated on the valid specification) | — |
| `wrf_startup_refusal` | enforcing | DEFERRED/FAIL | yes (not raised on the valid specification) | yes |
| `wrf_startup_rewrite` | enforcing | FAIL/WARN | yes (not raised on the valid specification) | yes |

| rule | injected fault | expected | detected | caught | other rules raised |
|---|---|---|---|---|---|
| `ratio_odd` | even grid ratio | FAIL | FAIL | yes | — |
| `nest_divisible` | inner grid not divisible | FAIL | FAIL | yes | — |
| `nest_inside_parent` | nest outside parent | FAIL | FAIL | yes | — |
| `evert_same` | vertical levels differ | FAIL | FAIL | yes | wrf_startup_refusal |
| `evert_min` | too few vertical levels | WARN | WARN | yes | — |
| `domain_min_100` | domain smaller than 100 cells | WARN | WARN | yes | nest_divisible |
| `pbl_sfclay_compat` | surface layer incompatible with PBL | FAIL | FAIL | yes | — |
| `domain_constraint` | shared option differs per domain | FAIL | FAIL | yes | wrf_startup_rewrite |
| `domain_constraint` | inner domain neither d01 nor zero | FAIL | FAIL | yes | wrf_startup_refusal |
| `urban_noah` | urban scheme without an urban-capable LSM | FAIL | FAIL | yes | wrf_startup_rewrite |
| `cam_full_sbm` | refused microphysics option | FAIL | FAIL | yes | wrf_startup_refusal |
| `cam_build_dependent` | option the build does not enable | FAIL | FAIL | yes | wrf_startup_refusal |
| `date_format` | unparsable date | FAIL | FAIL | yes | — |
| `period_positive` | end before start | FAIL | FAIL | yes | — |
| `required` | a required decision removed | FAIL | FAIL | yes | — |
| `per_domain_length` | per-domain list of the wrong length | FAIL | FAIL | yes | — |
| `urban_bep_pbl` | multi-layer urban scheme under a PBL outside the rule's set | FAIL | FAIL | yes | pbl_sfclay_compat |
| `pbl_sfclay_compat` | PBL without a compatibility entry | WARN | WARN | yes | urban_bep_pbl |
| `pbl_sfclay_compat` | surface layer 10 without PBL 10 | FAIL | FAIL | yes | wrf_startup_refusal |
| `cam_build_dependent` | option decided by the compiler, not a build flag | DEFERRED | DEFERRED | yes | — |
| `date_format` | unparsable end date | FAIL | FAIL | yes | — |
| `domain_constraint` | microphysics differs per domain | FAIL | FAIL | yes | wrf_startup_rewrite |
| `domain_constraint` | inner PBL neither d01 nor zero | FAIL | FAIL | yes | wrf_startup_refusal |
| `domain_constraint` | surface layer differs per domain | FAIL | FAIL | yes | wrf_startup_refusal |
| `domain_constraint` | land surface differs per domain | FAIL | FAIL | yes | wrf_startup_refusal |
| `domain_constraint` | long-wave radiation differs per domain | FAIL | FAIL | yes | wrf_startup_refusal |
| `domain_constraint` | short-wave radiation differs per domain | FAIL | FAIL | yes | wrf_startup_refusal |
| `wrf_startup_refusal` | wrf.exe refuses a surface layer that differs per domain | FAIL | FAIL | yes | domain_constraint |
| `wrf_startup_rewrite` | wrf.exe rewrites the urban option on the outer domains | FAIL | FAIL | yes | domain_constraint |
| `cam_build_dependent` | option the recorded build enables | PASS | PASS | yes | — |

- Approval invalidated by **5/5** post-approval edits; still valid after **3/3** non-edits

| post-approval edit | approval still valid |
|---|---|
| approved value changed in the specification | no |
| draft value changed after approval | no |
| request rewritten after approval | no |
| discussion record replaced | no |
| review form edited after approval | no |
| no edit | yes |
| specification rewritten with identical bytes | yes |
| specification re-serialised with the same content | yes |

## Edit provenance (the pre-filled form as the decision surface)

| property | holds |
|---|---|
| agent value retained | yes |
| edit marked | yes |
| value is the person s choice | yes |
| changed listed in spec | yes |
| agent reason survives | yes |

- Re-emission byte-identical: **True** (namelist.wps: c40203a20691, namelist.input: a4574f0d4014)
- Provenance complete: **True** across 111 emitted parameters

## WRF start-up checks evaluated on the generated namelist

- Known cases reproduced: **3/3**
- Statements compiled out for the recorded build: 22
- Decidable before the run: refusals 78/85, rewrites 241/268; study-defining refusals 37/37, rewrites 95/114, replacements 20/21
