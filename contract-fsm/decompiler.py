#!/usr/bin/env python3
"""
decompiler.py — FSMDefinition JSON → Deterministic English

    python decompiler.py fsm.json [-o contract_english.txt]

DETERMINISM: Same input → same output. No LLMs. No randomness. Diff-safe.
"""

import json, sys
from collections import defaultdict

# ── Humaniser maps ───────────────────────────────────────────────────────────

RULE_LABELS = {
    "OBLIGATION": "Obligation",
    "PROHIBITION": "Prohibition",
    "PERMISSION": "Permission",
    "POWER": "Power",
    "IMMUNITY": "Immunity",
    "REPRESENTATION": "Representation",
    "WARRANTY": "Warranty",
    "COVENANT": "Covenant",
}

TRIGGER_LABELS = {
    "DATE_TRIGGER": "On date",
    "EVENT_TRIGGER": "Upon event",
    "CONDITION_PRECEDENT": "When condition precedent holds",
    "CONDITION_SUBSEQUENT": "When condition subsequent holds",
    "THRESHOLD_TRIGGER": "When threshold crossed",
    "NOTICE_TRIGGER": "Upon notice",
    "TEMPORAL_TRIGGER": "After time period",
    "ABSENCE_TRIGGER": "Upon absence of event",
}

EFFECT_LABELS = {
    "PAYMENT": "Payment",
    "ASSET_TRANSFER": "Asset transfer",
    "STATE_TRANSITION": "State transition",
    "RULE_ACTIVATION": "Rule activation",
    "CURE_WINDOW": "Cure window",
    "ACCELERATION": "Acceleration",
    "FORFEITURE": "Forfeiture",
    "TERMINATION": "Termination",
    "RETROACTIVE_REVISION": "Retroactive revision",
}

CONSTRAINT_LABELS = {
    "CAP": "Maximum cap",
    "FLOOR": "Minimum floor",
    "GRACE_PERIOD": "Grace period",
    "AGGREGATE_CONSTRAINT": "Aggregate constraint",
    "PRO_RATA": "Pro rata allocation",
    "CARVE_OUT": "Carve-out",
    "CONTROLLING_ITEM": "Controlling provision",
}

OPERATOR_MAP = [
    (">=", "≥"), ("<=", "≤"), ("!=", "≠"), ("==", "="),
    (">", ">"), ("<", "<"),
]

ACTIVATION_LABELS = {
    "INACTIVE": "inactive at formation",
    "ACTIVE": "active at formation",
    "FULFILLED": "fulfilled",
    "VIOLATED": "violated",
}


def humanize(expr: str) -> str:
    if not expr:
        return ""
    result = expr
    for op, sym in OPERATOR_MAP:
        result = result.replace(op, f" {sym} ")
    return result.replace("_", " ")


def fmt_money(val) -> str:
    try:
        return f"${float(val):,.2f}"
    except:
        return str(val)

def fmt_rate(val) -> str:
    try:
        v = float(val)
        return f"{v * 100:.2f}%" if v < 1 else f"{v}%"
    except:
        return str(val)


# ── Decompiler ───────────────────────────────────────────────────────────────

def decompile(fsm: dict) -> str:
    out = []
    cid = fsm["contract_id"]
    parties = sorted(fsm.get("parties", []))
    states = sorted(fsm.get("states", []), key=lambda s: s["id"])
    transitions = sorted(fsm.get("transitions", []), key=lambda t: t["id"])
    rules = sorted(fsm.get("rules", []), key=lambda r: r["id"])
    params = fsm.get("params", {})
    initial = fsm["initial_state"]
    unmappable = sorted(fsm.get("unmappable_rules", []), key=lambda r: r.get("id", ""))

    # ── Header ──
    out.append("=" * 64)
    out.append(f"CONTRACT: {cid}")
    out.append("=" * 64)
    out.append("")

    # ── Parties ──
    out.append("PARTIES")
    out.append("─" * 40)
    for i, p in enumerate(parties, 1):
        out.append(f"  {i}. {p}")
    out.append("")

    # ── Contract Parameters ──
    out.append("CONTRACT PARAMETERS")
    out.append("─" * 40)

    amounts = params.get("amounts", {})
    if amounts:
        out.append("  Amounts:")
        for k in sorted(amounts):
            out.append(f"    {k.replace('_', ' ').title()}: {fmt_money(amounts[k])}")

    rates = params.get("rates", {})
    if rates:
        out.append("  Rates:")
        for k in sorted(rates):
            out.append(f"    {k.replace('_', ' ').title()}: {fmt_rate(rates[k])}")

    durations = params.get("durations", {})
    if durations:
        out.append("  Durations:")
        for k in sorted(durations):
            out.append(f"    {k.replace('_', ' ').title()}: {durations[k]} days")

    thresholds = params.get("thresholds", {})
    if thresholds:
        out.append("  Thresholds:")
        for k in sorted(thresholds):
            out.append(f"    {k.replace('_', ' ').title()}: {thresholds[k]}")

    base_dates = params.get("base_dates", {})
    if base_dates:
        out.append("  Key Dates:")
        for k in sorted(base_dates):
            out.append(f"    {k.replace('_', ' ').title()}: {base_dates[k] or '(to be determined)'}")
    out.append("")

    # ── States ──
    out.append("CONTRACT STATES")
    out.append("─" * 40)
    for s in states:
        mark = ""
        if s["id"] == initial:
            mark = " ← initial"
        if s.get("terminal"):
            mark = " ← terminal"
        active_rules = s.get("active_rule_ids", [])
        rule_note = f"  [rules: {', '.join(active_rules)}]" if active_rules else ""
        out.append(f"  {s['description']}{mark}{rule_note}")
    out.append("")

    # ── Rules ──
    mappable_rules = [r for r in rules if r.get("mappable", True)]
    if mappable_rules:
        out.append("RULES AND OBLIGATIONS")
        out.append("─" * 40)
        for r in mappable_rules:
            rtype = RULE_LABELS.get(r.get("type", ""), r.get("type", ""))
            activation = ACTIVATION_LABELS.get(r.get("activation", ""), "")
            out.append(f"  Rule {r['id']}: {rtype} ({activation})")
            out.append(f"    {r.get('party', '?')} → {r.get('counterparty', '?')}: {r.get('action', '')}")
            if r.get("description"):
                out.append(f"    {r['description']}")

            # Triggers
            for trig in r.get("triggers", []):
                ttype = TRIGGER_LABELS.get(trig.get("type", ""), trig.get("type", ""))
                out.append(f"    Trigger: {ttype} — {trig.get('condition', '')}")
                if trig.get("duration"):
                    out.append(f"      Duration: {trig['duration']}")
                if trig.get("threshold_field"):
                    op = trig.get("threshold_operator", "")
                    out.append(f"      Threshold: {trig['threshold_field']} {op} {trig.get('threshold_value', '')}")

            # Effects (penalties etc.)
            for eff in r.get("effects", []):
                elabel = EFFECT_LABELS.get(eff.get("type", ""), eff.get("type", ""))
                out.append(f"    Effect: {elabel} — {eff.get('description', '')}")
                if eff.get("payment_formula"):
                    out.append(f"      Formula: {humanize(eff['payment_formula'])}")
                if eff.get("payment_from") or eff.get("payment_to"):
                    out.append(f"      From: {eff.get('payment_from', '?')} → To: {eff.get('payment_to', '?')}")
                if eff.get("cap"):
                    out.append(f"      Cap: {fmt_money(eff['cap'])}")

            # Constraints
            for con in r.get("constraints", []):
                clabel = CONSTRAINT_LABELS.get(con.get("type", ""), con.get("type", ""))
                out.append(f"    Constraint: {clabel}")
                if con.get("value"):
                    out.append(f"      Value: {fmt_money(con['value'])}")
                if con.get("duration"):
                    out.append(f"      Duration: {con['duration']}")
                if con.get("description"):
                    out.append(f"      {con['description']}")

            # Exceptions
            for exc in r.get("exceptions", []):
                out.append(f"    Exception: {exc}")

            out.append("")

    # ── FSM Transitions ──
    out.append("STATE TRANSITIONS")
    out.append("─" * 40)

    by_state = defaultdict(list)
    for t in transitions:
        for fs in t.get("from_states", []):
            by_state[fs].append(t)

    for s in states:
        tlist = by_state.get(s["id"], [])
        if not tlist:
            continue
        out.append(f"  When in state \"{s['description']}\":")
        out.append("")
        for t in sorted(tlist, key=lambda x: x["id"]):
            tgt = next((st for st in states if st["id"] == t["to_state"]), None)
            tgt_desc = tgt["description"] if tgt else t["to_state"]
            out.append(f"    On event \"{t['event_type'].replace('_', ' ')}\":")
            if t.get("guard"):
                out.append(f"      Guard: {humanize(t['guard'])}")
            if t.get("description"):
                out.append(f"      {t['description']}")
            for eff in t.get("effects", []):
                elabel = EFFECT_LABELS.get(eff.get("type", ""), eff.get("type", ""))
                edesc = eff.get("description", "")
                if eff.get("payment_formula"):
                    out.append(f"      → {elabel}: {edesc} [{humanize(eff['payment_formula'])}]")
                else:
                    out.append(f"      → {elabel}: {edesc}")
            same = all(fs == t["to_state"] for fs in t.get("from_states", []))
            if same:
                out.append(f"      Remains in \"{s['description']}\".")
            else:
                out.append(f"      Transitions to \"{tgt_desc}\".")
            out.append("")

    # ── Unmappable ──
    if unmappable:
        out.append("UNMODELED RULES")
        out.append("─" * 40)
        out.append("  The following rules could not be formally modeled:")
        out.append("")
        for r in unmappable:
            rtype = RULE_LABELS.get(r.get("type", ""), r.get("type", ""))
            out.append(f"  {r.get('id', '?')}: [{rtype}] {r.get('description', '')}")
            if r.get("unmappable_reason"):
                out.append(f"    Reason: {r['unmappable_reason']}")
            out.append("")

    out.append("=" * 64)
    out.append("END OF CONTRACT")
    out.append("=" * 64)

    return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        print("Usage: python decompiler.py <fsm.json> [-o output.txt]")
        print("\nDeterministic. No LLMs. No randomness. Diff-safe.")
        sys.exit(1)

    fsm_path = sys.argv[1]
    out_path = None
    if "-o" in sys.argv:
        idx = sys.argv.index("-o")
        out_path = sys.argv[idx + 1]

    with open(fsm_path) as f:
        fsm = json.load(f)

    english = decompile(fsm)

    if out_path:
        with open(out_path, "w") as f:
            f.write(english)
        print(f"✓ Decompiled: {fsm_path} → {out_path}")
    else:
        print(english)


if __name__ == "__main__":
    main()
