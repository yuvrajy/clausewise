#!/usr/bin/env python3
"""
compiler.py — FSMDefinition JSON → Standalone Python Contract Module

    python compiler.py fsm.json -o contract_westex.py
    python contract_westex.py scenario.json
"""

import json, sys
from datetime import datetime


def py_literal(obj, indent=2) -> str:
    """json.dumps but Python-safe: True/False/None instead of true/false/null."""
    s = json.dumps(obj, indent=indent)
    s = s.replace(': true', ': True').replace(': false', ': False').replace(': null', ': None')
    s = s.replace('[true', '[True').replace('[false', '[False').replace('[null', '[None')
    s = s.replace(', true', ', True').replace(', false', ', False').replace(', null', ', None')
    return s


def load_fsm(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def compile_contract(fsm: dict) -> str:
    contract_id = fsm["contract_id"]
    parties = fsm.get("parties", [])
    states = fsm.get("states", [])
    transitions = fsm.get("transitions", [])
    initial_state = fsm["initial_state"]
    params = fsm.get("params", {})
    rules = fsm.get("rules", [])
    unmappable = fsm.get("unmappable_rules", [])

    terminal_ids = [s["id"] for s in states if s.get("terminal")]

    L = []
    L.append('#!/usr/bin/env python3')
    L.append('"""')
    L.append(f'Auto-generated executable contract: {contract_id}')
    L.append(f'Generated: {datetime.now().isoformat()}')
    L.append(f'Parties: {", ".join(parties)}')
    if unmappable:
        L.append('')
        L.append('UNMODELED RULES (flagged, not silently dropped):')
        for u in unmappable:
            L.append(f'  - [{u.get("type","?")}] {u.get("description","")}')
            if u.get("unmappable_reason"):
                L.append(f'    Reason: {u["unmappable_reason"]}')
    L.append('')
    L.append('Usage: python <this_file>.py scenario.json')
    L.append('"""')
    L.append('')
    L.append('import json, sys, math')
    L.append('from decimal import Decimal, ROUND_HALF_UP')
    L.append('from datetime import date, timedelta')
    L.append('')

    # ── Contract data ──
    L.append('# ═══════════════════════════════════════════')
    L.append('# CONTRACT DATA')
    L.append('# ═══════════════════════════════════════════')
    L.append('')
    L.append(f'CONTRACT_ID = {json.dumps(contract_id)}')
    L.append(f'PARTIES = {json.dumps(parties)}')
    L.append(f'INITIAL_STATE = {json.dumps(initial_state)}')
    L.append(f'TERMINAL_STATES = {json.dumps(terminal_ids)}')
    L.append('')

    # Params
    L.append(f'PARAMS = {py_literal(params)}')
    L.append('')

    # States
    L.append(f'STATES = {py_literal({s["id"]: s for s in states})}')
    L.append('')

    # Build initial fields from params
    L.append('INITIAL_FIELDS = {}')
    L.append('for k, v in PARAMS.get("amounts", {}).items():')
    L.append('    try: INITIAL_FIELDS[k] = float(v)')
    L.append('    except: INITIAL_FIELDS[k] = v')
    L.append('for k, v in PARAMS.get("rates", {}).items():')
    L.append('    try: INITIAL_FIELDS[k] = float(v)')
    L.append('    except: INITIAL_FIELDS[k] = v')
    L.append('for k, v in PARAMS.get("durations", {}).items():')
    L.append('    INITIAL_FIELDS[k] = v')
    L.append('for k, v in PARAMS.get("thresholds", {}).items():')
    L.append('    try: INITIAL_FIELDS[k] = float(v)')
    L.append('    except: INITIAL_FIELDS[k] = v')
    L.append('for k, v in PARAMS.get("base_dates", {}).items():')
    L.append('    INITIAL_FIELDS[k] = v')
    L.append('')

    # Scan transitions for dynamic fields that need zero-init
    import re
    referenced = set()
    for t in transitions:
        for eff in t.get("effects", []):
            tgt = eff.get("target", "")
            if tgt:
                referenced.add(tgt)
        g = t.get("guard") or ""
        for tok in re.findall(r'[a-zA-Z_]\w*', g):
            if tok not in ("and", "or", "not", "True", "False", "None", "event",
                           "min", "max", "abs", "round"):
                referenced.add(tok)

    L.append('# Auto-init dynamic fields referenced in transitions')
    for field_name in sorted(referenced):
        L.append(f'INITIAL_FIELDS.setdefault({json.dumps(field_name)}, 0)')
    L.append('')

    # Rules (for rule activation tracking)
    L.append(f'RULES = {py_literal({r["id"]: r for r in rules})}')
    L.append('')

    # Transitions
    L.append(f'TRANSITIONS = {py_literal(transitions)}')
    L.append('')

    # ── Executor engine ──
    L.append(r'''
# ═══════════════════════════════════════════
# SAFE EXPRESSION EVALUATOR
# ═══════════════════════════════════════════

def safe_eval(expr, fields, event_details):
    if expr is None:
        return True
    ns = {
        "min": min, "max": max, "abs": abs, "round": round,
        "Decimal": Decimal, "ROUND_HALF_UP": ROUND_HALF_UP,
        "True": True, "False": False, "None": None,
        "true": True, "false": False, "null": None,
    }
    ns.update(fields)
    ns.update(event_details)           # natural names: days_late, payload_weight_kg, etc.
    for k, v in event_details.items():
        ns[f"event_{k}"] = v           # also available as event_days_late, etc.
    ns["event"] = event_details
    try:
        return eval(expr, {"__builtins__": {}}, ns)
    except Exception as e:
        print(f"  [WARN] expr: {expr!r} → {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════
# FSM EXECUTOR
# ═══════════════════════════════════════════

def apply_effect(eff, fields, event_details, narrative):
    etype = eff.get("type", "")
    desc = eff.get("description", "")
    payment_val = None

    # ── Type-specific narration and payment tracking ──
    if etype == "PAYMENT":
        pformula = eff.get("payment_formula", "0")
        pval = safe_eval(pformula, fields, event_details)
        if pval is not None:
            cap = eff.get("cap")
            if cap is not None:
                pval = min(pval, float(cap))
            pf = eff.get("payment_from", "")
            pt = eff.get("payment_to", "")
            fee_key = f"paid_{eff.get('id', 'misc')}"
            fields[fee_key] = fields.get(fee_key, 0) + pval
            narrative.append(f"Payment: {desc} — ${pval:,.2f} ({pf} → {pt})")
            payment_val = pval

    elif etype == "TERMINATION":
        narrative.append(f"Termination: {desc}")
    elif etype == "ACCELERATION":
        narrative.append(f"Acceleration: {desc}")
    elif etype == "FORFEITURE":
        narrative.append(f"Forfeiture: {desc}")
    elif etype == "CURE_WINDOW":
        narrative.append(f"Cure window: {desc}")
    elif etype == "RETROACTIVE_REVISION":
        narrative.append(f"Retroactive revision: {desc}")

    # ── Always apply target/formula if present ──
    target = eff.get("target", "")
    formula = eff.get("formula", "")
    if target and formula:
        val = safe_eval(formula, fields, event_details)
        if val is not None:
            action = eff.get("action", "assign")
            if action == "accumulate":
                fields[target] = fields.get(target, 0) + val
            else:
                fields[target] = val

    # STATE_TRANSITION override
    new_st = eff.get("new_state")
    if new_st:
        return ("STATE_OVERRIDE", new_st)

    return payment_val


def execute(scenario_events):
    state = INITIAL_STATE
    fields = dict(INITIAL_FIELDS)
    rule_states = {rid: r.get("activation", "INACTIVE") for rid, r in RULES.items()}
    narrative = []
    log = []
    financial = {}

    for step_num, event in enumerate(scenario_events, 1):
        event_type = event.get("event_type", "")
        details = event.get("details", {})
        party = event.get("party", "")
        ts = event.get("timestamp", "")

        entry = {
            "step": step_num,
            "timestamp": ts,
            "state_before": state,
            "event_type": event_type,
            "party": party,
            "transition_fired": None,
            "state_after": state,
        }

        if state in TERMINAL_STATES:
            entry["note"] = "Terminal — event ignored"
            log.append(entry)
            continue

        # Find matching transitions
        candidates = [
            t for t in TRANSITIONS
            if state in t.get("from_states", []) and t.get("event_type") == event_type
        ]

        fired = False
        for t in candidates:
            guard = t.get("guard")
            if guard is None or safe_eval(guard, fields, details):
                state_override = None
                for eff in t.get("effects", []):
                    result = apply_effect(eff, fields, details, narrative)
                    if isinstance(result, tuple) and result[0] == "STATE_OVERRIDE":
                        state_override = result[1]
                    elif isinstance(result, (int, float)):
                        eff_id = eff.get("id", "misc")
                        financial[eff_id] = financial.get(eff_id, 0) + result

                # Only trust state_override if it's a known FSM state.
                # Embedded rule effects sometimes emit STATE_TRANSITION with
                # lowercase or stale state names that don't match the FSM graph.
                if state_override and state_override in STATES:
                    state = state_override
                else:
                    state = t["to_state"]
                entry["transition_fired"] = t["id"]
                entry["state_after"] = state
                entry["description"] = t.get("description", "")
                fired = True
                break

        if not fired:
            entry["note"] = f"No transition for '{event_type}' in '{state}'"

        entry["fields_snapshot"] = dict(fields)
        log.append(entry)

    return {
        "contract_id": CONTRACT_ID,
        "final_state": state,
        "fields": fields,
        "financial_summary": financial,
        "rule_states": rule_states,
        "narrative": narrative,
        "log": log,
    }


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <scenario.json>")
        print(f"\nContract: {CONTRACT_ID}")
        print(f"Parties: {PARTIES}")
        print(f"States: {list(STATES.keys())}")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        scenario = json.load(f)

    events = scenario if isinstance(scenario, list) else scenario.get("events", [])
    result = execute(events)

    print(f"\n{'═' * 60}")
    print(f"CONTRACT: {result['contract_id']}")
    print(f"{'═' * 60}")
    print(f"\nFinal State: {result['final_state']}")

    if result['financial_summary']:
        print(f"\nFinancial Summary:")
        for k, v in sorted(result['financial_summary'].items()):
            print(f"  {k}: ${v:,.2f}")

    if result['narrative']:
        print(f"\nNarrative ({len(result['narrative'])} entries):")
        for n in result['narrative']:
            print(f"  → {n}")

    print(f"\nExecution Log ({len(result['log'])} steps):")
    for e in result['log']:
        tid = e.get('transition_fired', '—')
        note = e.get('note', '')
        desc = e.get('description', '')
        print(f"  {e['step']}: [{e['state_before']}] --{e['event_type']}--> [{e['state_after']}]  ({tid}) {desc} {note}")

    with open("execution_result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nFull result → execution_result.json")


if __name__ == "__main__":
    main()
''')

    return '\n'.join(L)


def main():
    if len(sys.argv) < 2:
        print("Usage: python compiler.py <fsm.json> [-o output.py]")
        sys.exit(1)

    fsm_path = sys.argv[1]
    output_path = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        output_path = sys.argv[idx + 1]

    fsm = load_fsm(fsm_path)

    if not output_path:
        safe = fsm["contract_id"].lower().replace(" ", "_").replace("-", "_")[:40]
        output_path = f"contract_{safe}.py"

    code = compile_contract(fsm)
    with open(output_path, "w") as f:
        f.write(code)

    print(f"✓ Compiled: {fsm_path} → {output_path}")
    print(f"  Run: python {output_path} <scenario.json>")


if __name__ == "__main__":
    main()
