"""
validator.py — Step 3: Pure-Python validation of an FSMDefinition.

No LLM. Checks structural correctness of the assembled FSM:
  1. Reachability — every state reachable from initial_state
  2. Dead ends — every non-terminal state has outbound transitions
  3. Terminal reachability — at least one reachable terminal state
  4. Rule references — all rule IDs cited in states/effects exist
  5. Formula syntax — payment_formula strings are valid Python
  6. Caps — constraint values must be non-negative
"""
from __future__ import annotations
import ast
from collections import deque

from .schema import FSMDefinition


def validate_fsm(fsm: FSMDefinition) -> list[str]:
    """
    Validate an FSMDefinition. Returns a list of error strings.
    Empty list means the FSM is valid.
    """
    errors: list[str] = []

    state_ids = {s.id for s in fsm.states}
    all_rule_ids = {r.id for r in fsm.rules + fsm.unmappable_rules}
    terminal_ids = {s.id for s in fsm.states if s.terminal}

    # ── 1. REACHABILITY ───────────────────────────────────────────────────────
    reachable: set[str] = set()
    queue: deque[str] = deque([fsm.initial_state])
    reachable.add(fsm.initial_state)
    while queue:
        current = queue.popleft()
        for tr in fsm.transitions:
            if current in tr.from_states and tr.to_state not in reachable:
                reachable.add(tr.to_state)
                queue.append(tr.to_state)

    for s in fsm.states:
        if s.id not in reachable:
            errors.append(f"State '{s.id}' is unreachable from initial state")

    # ── 2. DEAD ENDS ──────────────────────────────────────────────────────────
    has_outbound: set[str] = set()
    for tr in fsm.transitions:
        for sid in tr.from_states:
            has_outbound.add(sid)

    for s in fsm.states:
        if not s.terminal and s.id not in has_outbound:
            errors.append(
                f"Non-terminal state '{s.id}' has no outbound transitions (dead end)"
            )

    # ── 3. TERMINAL REACHABILITY ──────────────────────────────────────────────
    reachable_terminals = terminal_ids & reachable
    if not reachable_terminals:
        errors.append(
            "No reachable terminal state found — contract can never complete"
        )

    # ── 4. RULE REFERENCES ────────────────────────────────────────────────────
    referenced_rule_ids: set[str] = set()

    for s in fsm.states:
        for rid in s.active_rule_ids:
            referenced_rule_ids.add(rid)

    for tr in fsm.transitions:
        for effect in tr.effects:
            if isinstance(effect, dict) and "target_rule_id" in effect:
                rid = effect["target_rule_id"]
                if rid:
                    referenced_rule_ids.add(rid)

    for rid in referenced_rule_ids:
        if rid not in all_rule_ids:
            errors.append(f"Unknown rule ID '{rid}' referenced in state/transition")

    # ── 5. FORMULA SYNTAX ─────────────────────────────────────────────────────
    for tr in fsm.transitions:
        for effect in tr.effects:
            if not isinstance(effect, dict):
                continue
            formula = effect.get("payment_formula")
            if formula:
                try:
                    ast.parse(formula)
                except SyntaxError as err:
                    errors.append(
                        f"Bad payment formula in {tr.id}: {formula!r} — {err}"
                    )

    # ── 6. CAPS ───────────────────────────────────────────────────────────────
    for rule in fsm.rules + fsm.unmappable_rules:
        for c in rule.constraints:
            if c.value is not None and c.value < 0:
                errors.append(
                    f"Negative cap value {c.value} in constraint {c.id}"
                )

    return errors


def validate_and_report(fsm: FSMDefinition) -> tuple[bool, list[str]]:
    issues = validate_fsm(fsm)
    return (len(issues) == 0), issues
