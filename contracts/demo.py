#!/usr/bin/env python3
"""demo.py — run the full pipeline on a contract markdown file.
Usage: python -m contracts.demo path/to/contract.md

Output files written to output/<contract_id>/:
  english.txt       — decompiled English (round-trip fidelity check)
  fsm.json          — full FSM definition as JSON
  scenarios.txt     — scenario execution results with dollar amounts
  run.txt           — complete run log (everything printed to stdout)
"""
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from .pipeline import run_pipeline


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


def _fsm_to_dict(fsm) -> dict:
    """Serialize FSMDefinition to a plain dict for JSON output."""
    def rule_to_dict(r):
        return {
            "id": r.id,
            "type": r.type.value,
            "party": r.party,
            "counterparty": r.counterparty,
            "action": r.action,
            "activation": r.activation.value,
            "description": r.description,
            "mappable": r.mappable,
            "unmappable_reason": r.unmappable_reason,
            "triggers": [
                {k: v for k, v in {
                    "id": t.id, "type": t.type.value, "condition": t.condition,
                    "reference_date": t.reference_date, "duration": t.duration,
                    "duration_days": t.duration_days,
                    "threshold_field": t.threshold_field,
                    "threshold_value": t.threshold_value,
                    "threshold_operator": t.threshold_operator,
                }.items() if v is not None}
                for t in r.triggers
            ],
            "effects": [
                {k: v for k, v in {
                    "id": e.id, "type": e.type.value, "description": e.description,
                    "target_rule_id": e.target_rule_id,
                    "payment_formula": e.payment_formula,
                    "payment_from": e.payment_from, "payment_to": e.payment_to,
                    "cap": str(e.cap) if e.cap is not None else None,
                    "new_state": e.new_state,
                }.items() if v is not None}
                for e in r.effects
            ],
            "constraints": [
                {k: v for k, v in {
                    "id": c.id, "type": c.type.value,
                    "value": str(c.value) if c.value is not None else None,
                    "duration": c.duration, "duration_days": c.duration_days,
                    "scope": c.scope or None, "description": c.description or None,
                }.items() if v is not None}
                for c in r.constraints
            ],
            "exceptions": r.exceptions,
        }

    return {
        "contract_id": fsm.contract_id,
        "parties": fsm.parties,
        "initial_state": fsm.initial_state,
        "params": {
            "parties": fsm.params.parties,
            "base_dates": fsm.params.base_dates,
            "amounts": fsm.params.amounts,
            "rates": fsm.params.rates,
            "durations": fsm.params.durations,
            "thresholds": fsm.params.thresholds,
        },
        "states": [
            {
                "id": s.id, "description": s.description,
                "terminal": s.terminal, "active_rule_ids": s.active_rule_ids,
            }
            for s in fsm.states
        ],
        "transitions": [
            {
                "id": t.id, "from_states": t.from_states, "to_state": t.to_state,
                "event_type": t.event_type, "guard": t.guard,
                "effects": t.effects, "description": t.description,
            }
            for t in fsm.transitions
        ],
        "rules": [rule_to_dict(r) for r in fsm.rules],
        "unmappable_rules": [rule_to_dict(r) for r in fsm.unmappable_rules],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m contracts.demo <contract.md>")
        sys.exit(1)

    md = Path(sys.argv[1]).read_text()
    contract_id = Path(sys.argv[1]).stem
    result = run_pipeline(md, contract_id=contract_id, verbose=True)

    # ── Build output strings ──────────────────────────────────────────────────

    summary_lines = []
    summary_lines.append("\n" + "=" * 60)
    summary_lines.append("FSM SUMMARY")
    summary_lines.append("=" * 60)
    summary_lines.append(f"States: {[s.id for s in result.fsm.states]}")
    summary_lines.append(f"Initial: {result.fsm.initial_state}")
    summary_lines.append(f"Transitions: {len(result.fsm.transitions)}")
    summary_lines.append(f"Mappable rules: {len(result.fsm.rules)}")
    summary_lines.append(f"Unmappable rules: {len(result.fsm.unmappable_rules)}")

    if result.validation_issues:
        summary_lines.append("\nVALIDATION ISSUES:")
        for issue in result.validation_issues:
            summary_lines.append(f"  ! {issue}")

    scenario_lines = []
    scenario_lines.append("\n" + "=" * 60)
    scenario_lines.append("SCENARIO RESULTS")
    scenario_lines.append("=" * 60)
    for name, res in result.execution_results.items():
        scenario_lines.append(f"\n--- {name.upper()} ---")
        scenario_lines.append(f"  Final state: {res.final_state}")
        if res.financial_summary:
            scenario_lines.append("  Financials:")
            for k, v in res.financial_summary.items():
                scenario_lines.append(f"    {k}: ${v:,.2f}")
        else:
            scenario_lines.append("  Financials: (none)")
        scenario_lines.append("  Narrative (last 5 lines):")
        for line in res.narrative[-5:]:
            scenario_lines.append(f"    {line}")

    english_header = ["\n" + "=" * 60, "REGENERATED CONTRACT ENGLISH", "=" * 60]

    # ── Print to stdout ───────────────────────────────────────────────────────
    for line in summary_lines:
        print(line)
    for line in scenario_lines:
        print(line)
    for line in english_header:
        print(line)
    print(result.english[:2000])
    if len(result.english) > 2000:
        print(f"  ... ({len(result.english) - 2000} more chars)")

    # ── Save to output/<contract_id>/ ─────────────────────────────────────────
    out_dir = Path(__file__).parent.parent / "output" / contract_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # english.txt — full decompiled English
    (out_dir / "english.txt").write_text(result.english)

    # fsm.json — full FSM definition
    fsm_dict = _fsm_to_dict(result.fsm)
    (out_dir / "fsm.json").write_text(
        json.dumps(fsm_dict, indent=2, default=_decimal_default)
    )

    # scenarios.txt — scenario results
    (out_dir / "scenarios.txt").write_text("\n".join(scenario_lines))

    # report.txt — everything
    run_lines = summary_lines + scenario_lines + english_header + [result.english]
    (out_dir / "report.txt").write_text("\n".join(run_lines))

    print(f"\n✓ Results saved to output/{contract_id}/")
    print(f"    english.txt   — decompiled contract")
    print(f"    fsm.json      — full FSM (feed to contract-fsm/compiler.py)")
    print(f"    scenarios.txt — scenario results")
    print(f"    report.txt    — complete log")


if __name__ == "__main__":
    main()
