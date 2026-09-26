"""decider — the decision service behind Switchyard's ``judge.provider: http``.

One HTTP endpoint, ``POST /decide``: a DecisionState in, a Decision out (the contract
defined in the switchyard-decision fork, ``switchyard/lib/decision/provider.py``).
Backends are swappable without touching Switchyard:

  rules    static table (no model) — always available, the floor
  anyjev   AnyJev on a local Qwen3 (typed choice questions over logits) — needs a GPU node
  rl       a self-trained policy (bandit → LoRA) loaded from a checkpoint
  jev      forward to a TypeSafe-compatible /v1/systemone server (Jev, Kev, Decider)

Every decision is appended to logs/decisions.jsonl together with the state, so the
same file is the training set for the rl backend.
"""

__version__ = "0.1.0"
