"""
assembler.py — Step 2: LLM assembles Rules + ContractParams into an FSMDefinition.

Takes the extracted rules and builds a Finite State Machine that represents
the lifecycle of the contract.
"""
from __future__ import annotations
import json
import re

import anthropic

from .schema import (
    Rule, ContractParams, FSMDefinition, FSMState, FSMTransition,
)

MODEL = "claude-sonnet-4-5"

ASSEMBLER_SYSTEM = """\
You are a contract FSM architect. Given a list of extracted contract rules, \
identify the finite set of contract STATES and TRANSITIONS that represent \
the lifecycle of this contract.

━━━ STATES ━━━
State IDs MUST be SCREAMING_SNAKE_CASE. Keep the state set minimal — 6–12 \
states is almost always enough. Start with ACTIVE as the initial working state. \
End with FULFILLED or TERMINATED (or both) as terminal states. Common patterns:
  ACTIVE, DELIVERY_LATE, IN_CURE, PAST_DUE, AWAITING_ACCEPTANCE,
  HOLDBACK_PENDING, COMPLETED_WITH_PENALTY, FULFILLED, TERMINATED
Mark exactly one state as initial. Mark terminal states (no further obligations).

━━━ TRANSITIONS ━━━
Each transition has:
  - "id": "TR1", "TR2", … (sequential, never reuse)
  - "from_states": [list of state IDs this fires from]
  - "to_state": the destination state ID (this IS the state change — see below)
  - "event_type": snake_case verb string (e.g. "delivery_made", "payment_received")
  - "guard": Python boolean expression or null
  - "effects": list of PAYMENT/FORFEITURE/ASSET_TRANSFER/CURE_WINDOW effects only
  - "description": one sentence

CRITICAL — to_state IS the state change. Do NOT include STATE_TRANSITION or \
RULE_ACTIVATION effects inside a transition's effects list. Embedding \
extra state changes as effects creates ambiguity in the executor and will \
produce wrong results. The only way to change state is via "to_state".

━━━ EFFECTS (inside transitions) ━━━
Only these types belong inside a transition's effects list:
  PAYMENT          — money moves; requires payment_formula, payment_from, payment_to, cap
  FORFEITURE       — party loses a right; include description
  ASSET_TRANSFER   — non-money resource changes hands; include description
  CURE_WINDOW      — open a cure period; include duration_days

Effect IDs must be E1, E2, E3 … scoped per transition (reset to E1 for each \
transition). Do NOT use formats like E1_TR1 or TR2_E1.

━━━ PAYMENT FORMULAS ━━━
Python expressions evaluated at runtime. Available names:
  - All ContractParams amounts/rates/durations/thresholds by exact key name
  - Event detail fields passed at runtime (e.g. days_late, days_early, \
    payload_weight_kg, num_payloads, total_payloads, payloads_operating)
  - Builtins: min(), max(), abs(), int(), round()
  - Use the "cap" field for per-effect maximums — do NOT hardcode min() in formulas
  - Good: "days_late * daily_late_penalty"  with "cap": 2550000
  - Bad:  "min(days_late * 8500, 2550000)"  (obscures the cap, harder to audit)

━━━ GUARDS ━━━
Python boolean expressions. Reference event detail names directly \
(e.g. "days_late > 0", "payload_weight_kg > 20.31"). \
If a transition only fires under a condition, encode it in the guard. \
If it always fires, set guard to null.

Map every mappable Rule to at least one state or transition.
Output valid JSON only — no markdown fences, no commentary outside the JSON.

JSON schema:
{
  "states": [
    {"id": "ACTIVE", "description": "...", "terminal": false,
     "active_rule_ids": ["R1","R2"]}
  ],
  "transitions": [
    {"id": "TR1",
     "from_states": ["ACTIVE"],
     "to_state": "FULFILLED",
     "event_type": "payment_received",
     "guard": null,
     "effects": [
       {"id": "E1", "type": "PAYMENT",
        "payment_formula": "contract_price",
        "payment_from": "Client", "payment_to": "Developer",
        "cap": null,
        "description": "Full payment on acceptance"},
       {"id": "E2", "type": "PAYMENT",
        "payment_formula": "days_late * daily_late_penalty",
        "payment_from": "Developer", "payment_to": "Client",
        "cap": 10000,
        "description": "Late penalty deducted from payment"}
     ],
     "description": "Client pays developer on acceptance"}
  ],
  "initial_state": "ACTIVE"
}\
"""


def _strip_json(text: str) -> str:
    """Remove markdown code fences and fix common LLM JSON quirks."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _rule_to_compact(rule: Rule) -> dict:
    """Serialize a Rule to a compact dict with only non-null/non-empty fields."""
    d: dict = {
        "id": rule.id,
        "type": rule.type.value,
        "party": rule.party,
        "action": rule.action,
    }
    if rule.triggers:
        d["triggers"] = [
            {k: v for k, v in {
                "id": t.id,
                "type": t.type.value,
                "condition": t.condition,
                "reference_date": t.reference_date,
                "duration": t.duration,
                "duration_days": t.duration_days,
                "threshold_field": t.threshold_field,
                "threshold_value": t.threshold_value,
                "threshold_operator": t.threshold_operator,
            }.items() if v is not None}
            for t in rule.triggers
        ]
    if rule.effects:
        d["effects"] = [
            {k: v for k, v in {
                "id": e.id,
                "type": e.type.value,
                "description": e.description,
                "target_rule_id": e.target_rule_id,
                "payment_formula": e.payment_formula,
                "payment_from": e.payment_from,
                "payment_to": e.payment_to,
                "cap": str(e.cap) if e.cap is not None else None,
                "new_state": e.new_state,
            }.items() if v is not None}
            for e in rule.effects
        ]
    if rule.constraints:
        d["constraints"] = [
            {k: v for k, v in {
                "id": c.id,
                "type": c.type.value,
                "value": str(c.value) if c.value is not None else None,
                "duration": c.duration,
                "duration_days": c.duration_days,
                "scope": c.scope if c.scope else None,
                "description": c.description if c.description else None,
            }.items() if v is not None}
            for c in rule.constraints
        ]
    if rule.exceptions:
        d["exceptions"] = rule.exceptions
    return d


def assemble_fsm(
    rules: list[Rule],
    params: ContractParams,
    contract_id: str = "contract",
    feedback: list[str] | None = None,
    prior_state_ids: list[str] | None = None,
    verbose: bool = False,
) -> FSMDefinition:
    """
    Use an LLM to assemble a list of Rules + ContractParams into an FSMDefinition.
    If feedback (validator errors from a prior attempt) is provided, feed it back.
    If prior_state_ids (state IDs from a previous run) are provided, the model
    is instructed to reuse them so re-runs stay structurally consistent.
    """
    client = anthropic.Anthropic(timeout=120.0)

    mappable_rules = [r for r in rules if r.mappable]
    rule_dicts = [_rule_to_compact(r) for r in mappable_rules]

    params_summary = {
        "parties": params.parties,
        "amounts": params.amounts,
        "rates": params.rates,
        "durations": params.durations,
        "base_dates": params.base_dates,
        "thresholds": params.thresholds,
    }

    user_message = (
        f"EXTRACTED RULES:\n{json.dumps(rule_dicts, indent=2)}\n\n"
        f"CONTRACT PARAMETERS:\n{json.dumps(params_summary, indent=2)}\n\n"
        f"Build the FSM. Output valid JSON only."
    )

    if prior_state_ids:
        user_message += (
            "\n\nPRIOR STATE IDs (reuse these exact names where applicable — "
            "only add new ones if a required state is genuinely missing):\n  "
            + ", ".join(prior_state_ids)
        )

    if feedback:
        user_message += (
            "\n\nVALIDATION ERRORS FROM PRIOR ATTEMPT — fix these:\n"
            + "\n".join(feedback)
        )

    print(f"  [assembler] Sending {len(mappable_rules)} mappable rules to LLM …", flush=True)

    raw = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=16384,
        system=[{"type": "text", "text": ASSEMBLER_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for chunk in stream.text_stream:
            raw += chunk
            if len(raw) % 400 < len(chunk):
                print(".", end="", flush=True)
    print(f" done ({len(raw)} chars)", flush=True)

    try:
        data = json.loads(_strip_json(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Assembler JSON parse failed: {exc}\n\nRaw:\n{raw[:800]}"
        ) from exc

    states = [
        FSMState(
            id=s["id"],
            description=s.get("description", ""),
            terminal=s.get("terminal", False),
            active_rule_ids=s.get("active_rule_ids", []),
        )
        for s in data.get("states", [])
    ]

    transitions = [
        FSMTransition(
            id=t["id"],
            from_states=t.get("from_states", []),
            to_state=t["to_state"],
            event_type=t["event_type"],
            guard=t.get("guard"),
            effects=t.get("effects", []),
            description=t.get("description", ""),
        )
        for t in data.get("transitions", [])
    ]

    if verbose:
        print(
            f"  [assembler] FSM built: {len(states)} states, "
            f"{len(transitions)} transitions"
        )

    return FSMDefinition(
        contract_id=contract_id,
        parties=params.parties,
        states=states,
        transitions=transitions,
        initial_state=data["initial_state"],
        rules=[r for r in rules if r.mappable],
        params=params,
        unmappable_rules=[r for r in rules if not r.mappable],
    )
