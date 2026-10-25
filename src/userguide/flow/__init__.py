"""flow — the case lifecycle (public preview: the parts the measurements use).

  state    durable journal (flow.jsonl): revision, attempts, claim leases, CAS transitions
  actions  the work each automatic stage performs (compile, approval gate, prepare, verify)
  registry specialist ownership of design fields (specialists.yaml)

The stage runner, gate engine, specialist board and design discussion are not part of this preview.
"""

from . import actions, registry, state  # noqa: F401
