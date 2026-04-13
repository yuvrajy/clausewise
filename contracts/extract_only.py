#!/usr/bin/env python3
"""
extract_only.py — Run ONLY Step 1 (rule extraction) and print the raw JSON.

Usage: python3 -m contracts.extract_only contract-fsm/examples/ORBCOMM-Orbital-amendment-1-AIS-payload-procurement-2006.md
"""
from __future__ import annotations
import sys
import json
from pathlib import Path
from decimal import Decimal

import anthropic
import re


MODEL = "claude-sonnet-4-5"

PASS_A_SYSTEM = """\
You are a careful legal contract reader. Your job is to read a contract and \
produce a comprehensive narrative of EVERYTHING you notice. Do not try to \
structure or categorize yet — just notice and describe.

Cover: parties and their roles, every obligation (must-do), every prohibition \
(must-not-do), every permission (may-do), every deadline and what triggers it, \
every penalty or fee formula, every cap or ceiling, every grace period, every \
condition that changes the rules, every option or election, every termination \
right, and any ambiguities or edge cases you see.

Write in plain prose or bullets. Coverage is more important than organization.\
"""

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
      "triggers": [...],
      "effects": [...],
      "constraints": [...],
      "exceptions": [],
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


def _strip_json(text: str) -> str:
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


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 -m contracts.extract_only <contract.md>")
        sys.exit(1)

    path = Path(sys.argv[1])
    contract_markdown = path.read_text()
    print(f"Contract: {path.name}  ({len(contract_markdown)} chars)", flush=True)

    client = anthropic.Anthropic(timeout=120.0)

    # ── Pass A ────────────────────────────────────────────────────────────────
    print("\n[Pass A] Free reading …", flush=True)
    resp_a = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=[{"type": "text", "text": PASS_A_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"CONTRACT TEXT:\n\n{contract_markdown}"}],
    )
    prose = resp_a.content[0].text
    print(f"[Pass A] Done — {len(prose)} chars", flush=True)
    print("\n--- PASS A OUTPUT ---")
    print(prose)
    print("--- END PASS A ---\n", flush=True)

    # ── Pass B ────────────────────────────────────────────────────────────────
    print("[Pass B] Structured extraction …", flush=True)
    user_content = (
        f"PRIOR READING (use as ground truth):\n\n{prose}\n\n---\n\n"
        f"CONTRACT TEXT:\n\n{contract_markdown}\n\n---\n\nNow output the extraction JSON."
    )
    resp_b = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=[{"type": "text", "text": PASS_B_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    raw = resp_b.content[0].text
    print(f"[Pass B] Done — {len(raw)} chars", flush=True)

    # ── Parse & pretty-print ──────────────────────────────────────────────────
    cleaned = _strip_json(raw)
    try:
        data = json.loads(cleaned)
        rules = data.get("rules", [])
        params = data.get("params", {})
        print(f"\n✓ Parsed: {len(rules)} rules, params keys: {list(params.keys())}", flush=True)
        print("\n--- EXTRACTED JSON ---")
        print(json.dumps(data, indent=2))
        print("--- END JSON ---")
    except json.JSONDecodeError as exc:
        print(f"\n✗ JSON parse failed: {exc}", flush=True)
        print("\n--- RAW PASS B OUTPUT ---")
        print(raw)
        print("--- END RAW ---")


if __name__ == "__main__":
    main()
