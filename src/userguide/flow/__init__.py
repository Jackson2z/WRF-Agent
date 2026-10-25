"""flow — the case lifecycle as an executable state machine.

Layering (strict): the compiler (checks/emit) knows nothing about the flow; the flow only calls the compiler.
  dsl      stage graph: owners, executable gates, on: transitions, terminal states (validated at load)
  state    durable journal (flow.jsonl): revision, attempts, claim leases, CAS transitions
  gates    gate evaluation: artifact | python | command | all  -> pass / fail / wait
  actions  the work each automatic stage performs (compile, prepare, verify, restart)
  runner   the executor: action -> gate -> transition, with retries and back edges
  registry specialist ownership of design fields (specialists.yaml) + tool whitelists
  board    specialist handoff digests (five keys) in board.jsonl
  discussion  independent expert proposals -> cross-review -> synthesis; human select, never auto-approval
"""

from . import actions, board, discussion, dsl, gates, registry, runner, state  # noqa: F401
