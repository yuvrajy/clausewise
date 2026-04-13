"""
extractor.py — Step 1: LLM extracts Rules, Triggers, Effects, Constraints
from raw contract markdown.

Uses a two-pass approach:
  Pass A: Free-prose reading — model says everything it notices, unconstrained.
  Pass B: Structured extraction — model fills JSON schema, grounded in Pass A.

This prevents the schema from causing premature commitment that drops nuances.
"""
from __future__ import annotations
import json
import re
from decimal import Decimal
from typing import Optional

import anthropic

from .schema import (
    Rule, RuleType, ActivationState,
    Trigger, TriggerType,
    Effect, EffectType,
    Constraint, ConstraintType,
    ContractParams,
)

MODEL = "claude-sonnet-4-5"

# ── Pass A system prompt ──────────────────────────────────────────────────────

PASS_A_SYSTEM = """\
You are a careful legal contract reader. Read the contract and produce a \
dense bullet-point inventory of EVERYTHING normative in it. One bullet per \
observation. No prose paragraphs.

Cover every: party and role, obligation (must-do), prohibition (must-not-do), \
permission (may-do), deadline with its formula, penalty/fee with its exact \
dollar amount or rate, cap or ceiling with its exact value, grace period with \
its exact duration, condition that changes the rules, option or election, \
termination right, cross-clause dependency, and any ambiguity.

Format: tight bullets only. Extract every number exactly as written.\
"""

# ── Pass B system prompt ──────────────────────────────────────────────────────

PASS_B_SYSTEM = """\
You are a legal contract analyst. Using the contract text and the prior \
reading as your ground truth, extract every normative element as structured JSON.

Rule types:
  OBLIGATION   — party MUST do X by deadline
  PROHIBITION  — party MUST NOT do X
  PERMISSION   — party MAY do X (no violation possible)
  POWER        — party can change the contract structure (options, waivers)
  IMMUNITY     — party cannot have a rule imposed by another party's action
  REPRESENTATION — declaration of fact, checked once
  WARRANTY     — continuous declaration checked over a period
  COVENANT     — ongoing behavioral commitment

Trigger types: DATE_TRIGGER, EVENT_TRIGGER, CONDITION_PRECEDENT,
CONDITION_SUBSEQUENT, THRESHOLD_TRIGGER, NOTICE_TRIGGER, TEMPORAL_TRIGGER,
ABSENCE_TRIGGER

Effect types: PAYMENT, ASSET_TRANSFER, STATE_TRANSITION, RULE_ACTIVATION,
CURE_WINDOW, ACCELERATION, FORFEITURE, TERMINATION, RETROACTIVE_REVISION

Constraint types: CAP, FLOOR, GRACE_PERIOD, AGGREGATE_CONSTRAINT,
PRO_RATA, CARVE_OUT, CONTROLLING_ITEM

Output valid JSON only — no markdown fences, no commentary outside the JSON.
Schema:
{
  "rules": [
    {
      "id": "R1",
      "type": "<RuleType>",
      "party": "<who bears this rule>",
      "counterparty": "<who benefits>",
      "action": "<short description of what the rule governs>",
      "description": "<verbatim or paraphrased source text>",
      "triggers": [
        {
          "id": "T1",
          "type": "<TriggerType>",
          "condition": "<human-readable condition>",
          "reference_date": "<field name this is relative to, or null>",
          "duration": "<e.g. '90 days', or null>",
          "duration_days": <integer or null>,
          "threshold_field": "<field name to check, or null>",
          "threshold_value": "<value string, or null>",
          "threshold_operator": "<gt|lt|gte|lte|eq or null>"
        }
      ],
      "effects": [
        {
          "id": "E1",
          "type": "<EffectType>",
          "description": "<what happens>",
          "target_rule_id": "<Rule ID this activates/deactivates, or null>",
          "payment_formula": "<Python expression e.g. days_late * 500, or null>",
          "payment_from": "<party name or null>",
          "payment_to": "<party name or null>",
          "cap": <number or null>,
          "new_state": "<FSM state label or null>"
        }
      ],
      "constraints": [
        {
          "id": "C1",
          "type": "<ConstraintType>",
          "value": <number or null>,
          "duration": "<e.g. '15 days' or null>",
          "duration_days": <integer or null>,
          "scope": ["<effect_id or rule_id>"],
          "description": "<what this constrains>"
        }
      ],
      "exceptions": ["<Rule IDs that override this rule>"],
      "expiry": null
    }
  ],
  "params": {
    "parties": ["<party name>"],
    "base_dates": {"effective_date": null},
    "amounts": {"contract_price": "50000"},
    "rates": {},
    "durations": {"delivery_window_days": 90},
    "thresholds": {}
  }
}
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_json(text: str) -> str:
    """Remove markdown code fences and fix common LLM JSON quirks."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1).strip()
    else:
        # Sometimes models output { ... } with extra text before/after
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    # Remove trailing commas before } or ] (invalid JSON but common LLM output)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _close_truncated_json(text: str) -> str:
    """
    Best-effort recovery for JSON truncated at max_tokens.
    Closes any unclosed string, then balances brackets/braces.
    """
    # If we're in the middle of a string, close it
    # Count unescaped quotes to detect open string
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and in_string:
            i += 2
            continue
        if c == '"':
            in_string = not in_string
        i += 1
    if in_string:
        text += '"'

    # Remove any trailing incomplete key-value pair (e.g. "key": <nothing>)
    text = re.sub(r',?\s*"[^"]*"\s*:\s*$', '', text)
    # Remove trailing comma
    text = re.sub(r',\s*$', '', text)

    # Balance brackets and braces
    stack = []
    in_string = False
    for i, c in enumerate(text):
        if c == '\\' and in_string:
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c in '{[':
            stack.append(c)
        elif c in '}]':
            if stack:
                stack.pop()

    # Close everything that's still open
    close_map = {'{': '}', '[': ']'}
    for opener in reversed(stack):
        text += close_map[opener]

    return text


def _parse_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _safe_enum(enum_cls, value, default):
    """Parse an enum value, falling back to default if the LLM gives a bad value."""
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return enum_cls(default)


def _parse_trigger(d: dict) -> Trigger:
    return Trigger(
        id=d.get("id", "T?"),
        type=_safe_enum(TriggerType, d.get("type", "EVENT_TRIGGER"), "EVENT_TRIGGER"),
        condition=d.get("condition", ""),
        reference_date=d.get("reference_date"),
        duration=d.get("duration"),
        duration_days=d.get("duration_days"),
        threshold_field=d.get("threshold_field"),
        threshold_value=d.get("threshold_value"),
        threshold_operator=d.get("threshold_operator"),
    )


def _parse_effect(d: dict) -> Effect:
    return Effect(
        id=d.get("id", "E?"),
        type=_safe_enum(EffectType, d.get("type", "STATE_TRANSITION"), "STATE_TRANSITION"),
        description=d.get("description", ""),
        target_rule_id=d.get("target_rule_id"),
        payment_formula=d.get("payment_formula"),
        payment_from=d.get("payment_from"),
        payment_to=d.get("payment_to"),
        cap=_parse_decimal(d.get("cap")),
        new_state=d.get("new_state"),
    )


def _parse_constraint(d: dict) -> Constraint:
    return Constraint(
        id=d.get("id", "C?"),
        type=_safe_enum(ConstraintType, d.get("type", "CAP"), "CAP"),
        value=_parse_decimal(d.get("value")),
        duration=d.get("duration"),
        duration_days=d.get("duration_days"),
        scope=d.get("scope", []),
        description=d.get("description", ""),
    )


MAPPABLE_TYPES = {RuleType.OBLIGATION, RuleType.PROHIBITION}
# PERMISSION, POWER etc. are unmappable for now (user's scope = 4 primitives)
# But we still extract them so they can be attached in Step 4.

def _parse_rule(d: dict) -> Rule:
    rt = _safe_enum(RuleType, d.get("type", "OBLIGATION"), "OBLIGATION")
    mappable = rt in MAPPABLE_TYPES
    reason = None if mappable else f"{rt.value} is not in the four mappable primitives"
    return Rule(
        id=d.get("id", "R?"),
        type=rt,
        party=d.get("party", ""),
        counterparty=d.get("counterparty", ""),
        action=d.get("action", ""),
        activation=ActivationState.INACTIVE,
        triggers=[_parse_trigger(t) for t in d.get("triggers", [])],
        effects=[_parse_effect(e) for e in d.get("effects", [])],
        constraints=[_parse_constraint(c) for c in d.get("constraints", [])],
        exceptions=d.get("exceptions", []),
        expiry=_parse_trigger(d["expiry"]) if (d.get("expiry") and isinstance(d["expiry"], dict)) else None,
        description=d.get("description", ""),
        mappable=mappable,
        unmappable_reason=reason,
    )


def _parse_params(d: dict) -> ContractParams:
    return ContractParams(
        parties=d.get("parties", []),
        base_dates=d.get("base_dates", {"effective_date": None}),
        amounts=d.get("amounts", {}),
        rates=d.get("rates", {}),
        durations=d.get("durations", {}),
        thresholds=d.get("thresholds", {}),
    )


# ── Main extraction function ──────────────────────────────────────────────────

def extract_rules(
    contract_markdown: str,
    verbose: bool = False,
) -> tuple[list[Rule], ContractParams]:
    """
    Two-pass extraction:
      Pass A: free prose reading (unconstrained)
      Pass B: structured JSON extraction grounded in Pass A
    Returns (all_rules, params) where all_rules includes unmappable ones.
    """
    client = anthropic.Anthropic(timeout=120.0)

    # ── Pass A: free prose reading ─────────────────────────────────────────
    print("  [extractor] Pass A: free reading …", flush=True)

    prose_reading = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=2048,
        system=[{"type": "text", "text": PASS_A_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"CONTRACT TEXT:\n\n{contract_markdown}"}],
    ) as stream:
        for chunk in stream.text_stream:
            prose_reading += chunk
            if len(prose_reading) % 400 < len(chunk):
                print(".", end="", flush=True)
    print(f" done ({len(prose_reading)} chars)", flush=True)

    # ── Pass B: structured extraction grounded in Pass A ───────────────────
    print("  [extractor] Pass B: structured extraction …", flush=True)

    user_content = (
        f"PRIOR READING (use as ground truth — do not drop anything it identified):\n\n"
        f"{prose_reading}\n\n"
        f"---\n\n"
        f"CONTRACT TEXT:\n\n{contract_markdown}\n\n"
        f"---\n\n"
        f"Now output the extraction JSON."
    )

    raw = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=[{"type": "text", "text": PASS_B_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for chunk in stream.text_stream:
            raw += chunk
            if len(raw) % 400 < len(chunk):
                print(".", end="", flush=True)
    print(f" done ({len(raw)} chars)", flush=True)

    cleaned = _strip_json(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Second attempt: fix missing commas between adjacent values
        cleaned2 = re.sub(r'("|\d|true|false|null)\s*\n(\s*")', r'\1,\n\2', cleaned)
        try:
            data = json.loads(cleaned2)
        except json.JSONDecodeError:
            # Third attempt: recover from truncation at max_tokens
            cleaned3 = _close_truncated_json(cleaned2)
            try:
                data = json.loads(cleaned3)
            except json.JSONDecodeError:
                raise ValueError(f"Extractor JSON parse failed: {exc}\n\nRaw:\n{raw[:800]}") from exc

    rules = [_parse_rule(r) for r in data.get("rules", [])]
    params = _parse_params(data.get("params", {}))

    if verbose:
        mappable = sum(1 for r in rules if r.mappable)
        print(f"  [extractor] {len(rules)} rules extracted ({mappable} mappable)")

    return rules, params
