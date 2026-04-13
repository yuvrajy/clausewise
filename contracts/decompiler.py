"""
decompiler.py — Step 6: Deterministic template-based English generation.

NO LLM. Same input always produces identical output. Diff-safe.
Ported from contract-fsm/decompiler.py with an FSMDefinition → dict adapter.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal

from .schema import FSMDefinition


# ── Humaniser maps ────────────────────────────────────────────────────────────

RULE_LABELS = {
    "OBLIGATION": "Obligation", "PROHIBITION": "Prohibition",
    "PERMISSION": "Permission", "POWER": "Power",
    "IMMUNITY": "Immunity", "REPRESENTATION": "Representation",
    "WARRANTY": "WARRANTY", "COVENANT": "Covenant",
}

TRIGGER_LABELS = {
    "DATE_TRIGGER": "On date", "EVENT_TRIGGER": "Upon event",
    "CONDITION_PRECEDENT": "When condition precedent holds",
    "CONDITION_SUBSEQUENT": "When condition subsequent holds",
    "THRESHOLD_TRIGGER": "When threshold crossed",
    "NOTICE_TRIGGER": "Upon notice", "TEMPORAL_TRIGGER": "After time period",
    "ABSENCE_TRIGGER": "Upon absence of event",
}

EFFECT_LABELS = {
    "PAYMENT": "Payment", "ASSET_TRANSFER": "Asset transfer",
    "STATE_TRANSITION": "State transition", "RULE_ACTIVATION": "Rule activation",
    "CURE_WINDOW": "Cure window", "ACCELERATION": "Acceleration",
    "FORFEITURE": "Forfeiture", "TERMINATION": "Termination",
    "RETROACTIVE_REVISION": "Retroactive revision",
}

CONSTRAINT_LABELS = {
    "CAP": "Maximum cap", "FLOOR": "Minimum floor",
    "GRACE_PERIOD": "Grace period", "AGGREGATE_CONSTRAINT": "Aggregate constraint",
    "PRO_RATA": "Pro rata allocation", "CARVE_OUT": "Carve-out",
    "CONTROLLING_ITEM": "Controlling provision",
}

OPERATOR_MAP = [
    (">=", "≥"), ("<=", "≤"), ("!=", "≠"), ("==", "="),
    (">", ">"), ("<", "<"),
]

ACTIVATION_LABELS = {
    "INACTIVE": "inactive at formation", "ACTIVE": "active at formation",
    "FULFILLED": "fulfilled", "VIOLATED": "violated",
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
    except Exception:
        return str(val)


def fmt_rate(val) -> str:
    try:
        v = float(val)
        return f"{v * 100:.2f}%" if v < 1 else f"{v}%"
    except Exception:
        return str(val)


# ── FSMDefinition → plain dict ────────────────────────────────────────────────

def _trigger_to_dict(t) -> dict:
    return {
        "id": t.id,
        "type": t.type.value if hasattr(t.type, "value") else str(t.type),
        "condition": t.condition,
        "reference_date": t.reference_date,
        "duration": t.duration,
        "duration_days": t.duration_days,
        "threshold_field": t.threshold_field,
        "threshold_value": t.threshold_value,
        "threshold_operator": t.threshold_operator,
    }


def _effect_to_dict(e) -> dict:
    return {
        "id": e.id,
        "type": e.type.value if hasattr(e.type, "value") else str(e.type),
        "description": e.description,
        "target_rule_id": e.target_rule_id,
        "payment_formula": e.payment_formula,
        "payment_from": e.payment_from,
        "payment_to": e.payment_to,
        "cap": str(e.cap) if isinstance(e.cap, Decimal) else e.cap,
        "new_state": e.new_state,
    }


def _constraint_to_dict(c) -> dict:
    return {
        "id": c.id,
        "type": c.type.value if hasattr(c.type, "value") else str(c.type),
        "value": str(c.value) if isinstance(c.value, Decimal) else c.value,
        "duration": c.duration,
        "duration_days": c.duration_days,
        "scope": c.scope,
        "description": c.description,
    }


def _rule_to_dict(r) -> dict:
    return {
        "id": r.id,
        "type": r.type.value if hasattr(r.type, "value") else str(r.type),
        "party": r.party,
        "counterparty": r.counterparty,
        "action": r.action,
        "activation": r.activation.value if hasattr(r.activation, "value") else str(r.activation),
        "description": r.description,
        "mappable": r.mappable,
        "unmappable_reason": r.unmappable_reason,
        "triggers": [_trigger_to_dict(t) for t in r.triggers],
        "effects": [_effect_to_dict(e) for e in r.effects],
        "constraints": [_constraint_to_dict(c) for c in r.constraints],
        "exceptions": r.exceptions,
    }


def fsm_to_dict(fsm: FSMDefinition) -> dict:
    """Serialize an FSMDefinition dataclass to a plain dict for decompile()."""
    return {
        "contract_id": fsm.contract_id,
        "parties": fsm.parties,
        "initial_state": fsm.initial_state,
        "states": [
            {
                "id": s.id,
                "description": s.description,
                "terminal": s.terminal,
                "active_rule_ids": s.active_rule_ids,
            }
            for s in fsm.states
        ],
        "transitions": [
            {
                "id": t.id,
                "from_states": t.from_states,
                "to_state": t.to_state,
                "event_type": t.event_type,
                "guard": t.guard,
                "effects": t.effects,  # already list[dict] in FSMTransition
                "description": t.description,
            }
            for t in fsm.transitions
        ],
        "rules": [_rule_to_dict(r) for r in fsm.rules],
        "unmappable_rules": [_rule_to_dict(r) for r in fsm.unmappable_rules],
        "params": {
            "parties": fsm.params.parties,
            "amounts": fsm.params.amounts,
            "rates": fsm.params.rates,
            "durations": fsm.params.durations,
            "base_dates": fsm.params.base_dates,
            "thresholds": fsm.params.thresholds,
        },
    }


# ── Decompiler (ported from contract-fsm/decompiler.py) ──────────────────────

def _decompile_dict(fsm: dict) -> str:
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
            for trig in r.get("triggers", []):
                ttype = TRIGGER_LABELS.get(trig.get("type", ""), trig.get("type", ""))
                out.append(f"    Trigger: {ttype} — {trig.get('condition', '')}")
                if trig.get("duration"):
                    out.append(f"      Duration: {trig['duration']}")
                if trig.get("threshold_field"):
                    op = trig.get("threshold_operator", "")
                    out.append(f"      Threshold: {trig['threshold_field']} {op} {trig.get('threshold_value', '')}")
            for eff in r.get("effects", []):
                elabel = EFFECT_LABELS.get(eff.get("type", ""), eff.get("type", ""))
                out.append(f"    Effect: {elabel} — {eff.get('description', '')}")
                if eff.get("payment_formula"):
                    out.append(f"      Formula: {humanize(eff['payment_formula'])}")
                if eff.get("payment_from") or eff.get("payment_to"):
                    out.append(f"      From: {eff.get('payment_from', '?')} → To: {eff.get('payment_to', '?')}")
                if eff.get("cap"):
                    out.append(f"      Cap: {fmt_money(eff['cap'])}")
            for con in r.get("constraints", []):
                clabel = CONSTRAINT_LABELS.get(con.get("type", ""), con.get("type", ""))
                out.append(f"    Constraint: {clabel}")
                if con.get("value"):
                    out.append(f"      Value: {fmt_money(con['value'])}")
                if con.get("duration"):
                    out.append(f"      Duration: {con['duration']}")
                if con.get("description"):
                    out.append(f"      {con['description']}")
            for exc in r.get("exceptions", []):
                out.append(f"    Exception: {exc}")
            out.append("")

    # ── FSM Transitions (grouped by state) ──
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


# ── Public entry point (called by pipeline.py) ────────────────────────────────

def decompile(fsm: FSMDefinition) -> str:
    """
    Convert an FSMDefinition to a deterministic human-readable English summary.
    No LLM calls. Same input always produces identical output.
    """
    return _decompile_dict(fsm_to_dict(fsm))
