"""
scenario_gen.py — Step 4: LLM generates concrete test scenarios for the contract.

Produces 3-5 named scenario sequences (lists of Events) that exercise
different contract lifecycle paths.
"""
from __future__ import annotations
import json
import re
from datetime import date

import anthropic

from .schema import Event, FSMDefinition

MODEL = "claude-sonnet-4-5"

SCENARIO_GEN_SYSTEM = """\
You are a contract scenario designer. Given a contract and its FSM, generate \
3-5 concrete test scenarios as lists of events with specific dates and amounts. \
Each scenario must exercise a different contract path.

CRITICAL RULE — event_type values:
  You MUST copy event_type strings EXACTLY from the FSM TRANSITIONS list provided.
  Do NOT invent new event_type strings. Do NOT paraphrase or rename them.
  Only event_types that appear in the FSM transitions list are valid.

Required scenarios (generate all that apply to this contract):
  "happy_path"      — everything on time, accepted, paid
  "late_delivery"   — delivery past grace period, penalties accrue; include days_late in details
  "rejection_cure"  — first rejection, cure within window, then accepted
  "termination"     — one party terminates
  "edge_case"       — whatever path is most financially interesting

For each scenario output a list of Events in chronological order.
Use ISO dates (YYYY-MM-DD). Assume effective_date = 2025-01-01 unless stated.

For PAYMENT-triggering events include relevant numbers in details:
  days_late: int    — calendar days past the deadline (0 if on time)
  days_early: int   — calendar days before the deadline (0 if late)
  amount: number    — explicit payment amount if known

Output valid JSON only — no markdown fences, no commentary outside the JSON.
Schema:
{
  "scenarios": {
    "happy_path": [
      {"timestamp": "2025-04-01", "event_type": "delivery_made",
       "party": "Developer", "details": {"days_late": 0}}
    ]
  }
}
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


def _build_fsm_summary(fsm: FSMDefinition) -> str:
    lines = ["FSM SUMMARY", "==========="]
    lines.append(f"Contract: {fsm.contract_id}")
    lines.append(f"Parties: {', '.join(fsm.parties)}")
    lines.append(f"Initial state: {fsm.initial_state}")

    lines.append("\nSTATES:")
    for s in fsm.states:
        terminal_tag = " [TERMINAL]" if s.terminal else ""
        lines.append(f"  {s.id}{terminal_tag}: {s.description}")

    lines.append("\nTRANSITIONS:")
    for tr in fsm.transitions:
        guard_str = f" (guard: {tr.guard})" if tr.guard else ""
        lines.append(
            f"  {tr.id}: {'/'.join(tr.from_states)} -> {tr.to_state} "
            f"on [{tr.event_type}]{guard_str}"
        )

    lines.append("\nCONTRACT PARAMS:")
    p = fsm.params
    for k, v in p.amounts.items():
        lines.append(f"  amount.{k} = {v}")
    for k, v in p.durations.items():
        lines.append(f"  duration.{k} = {v} days")
    for k, v in p.base_dates.items():
        if v:
            lines.append(f"  date.{k} = {v}")
    for k, v in p.rates.items():
        lines.append(f"  rate.{k} = {v}")
    for k, v in p.thresholds.items():
        lines.append(f"  threshold.{k} = {v}")

    return "\n".join(lines)


def generate_scenarios(
    contract_markdown: str,
    fsm: FSMDefinition,
    verbose: bool = False,
) -> dict[str, list[Event]]:
    """
    Call the LLM to generate 3-5 named test scenarios for this contract/FSM.
    Returns a dict mapping scenario name -> list of Events in chronological order.
    """
    client = anthropic.Anthropic(timeout=120.0)

    fsm_summary = _build_fsm_summary(fsm)

    user_content = (
        f"CONTRACT TEXT:\n\n{contract_markdown}\n\n"
        f"---\n\n"
        f"{fsm_summary}\n\n"
        f"---\n\n"
        f"Generate 3-5 concrete test scenarios as JSON."
    )

    print("  [scenario_gen] Calling LLM to generate scenarios …", flush=True)

    raw = ""
    with client.messages.stream(
        model=MODEL,
        max_tokens=8192,
        system=[{"type": "text", "text": SCENARIO_GEN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
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
            f"Scenario gen JSON parse failed: {exc}\n\nRaw:\n{raw[:800]}"
        ) from exc

    scenarios: dict[str, list[Event]] = {}
    for name, event_list in data.get("scenarios", {}).items():
        events: list[Event] = []
        for e in event_list:
            events.append(
                Event(
                    timestamp=date.fromisoformat(e["timestamp"]),
                    event_type=e["event_type"],
                    party=e.get("party", ""),
                    details=e.get("details", {}),
                )
            )
        scenarios[name] = events

    if verbose:
        print(
            f"  [scenario_gen] {len(scenarios)} scenarios: {list(scenarios.keys())}"
        )

    return scenarios
