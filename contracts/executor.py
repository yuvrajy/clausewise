"""
executor.py — Step 5: Pure-Python simulation engine.

No LLM. Replays a list of Events against an FSMDefinition,
advancing state, firing effects, accumulating financials.
"""
from __future__ import annotations
import copy
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .schema import (
    ActivationState, ContractRuntime, ConstraintType, EffectType,
    Event, ExecutionResult, FSMDefinition, Rule, ScheduledEvent,
)


# ── Runtime creation ──────────────────────────────────────────────────────────

def create_runtime(fsm: FSMDefinition) -> ContractRuntime:
    """
    Initialise a fresh ContractRuntime from an FSMDefinition.
    Rules listed in the initial state's active_rule_ids get ACTIVE status.
    """
    all_rules: list[Rule] = fsm.rules + fsm.unmappable_rules
    rules_map: dict[str, Rule] = {r.id: copy.deepcopy(r) for r in all_rules}

    # Determine which rules are active in the initial state
    initial_state_obj = next(
        (s for s in fsm.states if s.id == fsm.initial_state), None
    )
    active_ids: set[str] = set(
        initial_state_obj.active_rule_ids if initial_state_obj else []
    )
    for rid, rule in rules_map.items():
        rule.activation = (
            ActivationState.ACTIVE if rid in active_ids else ActivationState.INACTIVE
        )

    # Build fields: amounts → Decimal, base_dates and durations as-is
    fields: dict[str, Any] = {}
    for k, v in fsm.params.amounts.items():
        try:
            fields[k] = Decimal(str(v))
        except Exception:
            fields[k] = v
    for k, v in fsm.params.base_dates.items():
        fields[k] = v
    for k, v in fsm.params.durations.items():
        fields[k] = v
    for k, v in fsm.params.rates.items():
        fields[k] = v
    for k, v in fsm.params.thresholds.items():
        fields[k] = v

    return ContractRuntime(
        state=fsm.initial_state,
        rules=rules_map,
        assets={},
        fields=fields,
        event_log=[],
        pending_events=[],
        computed_cache={},
        narrative=[],
    )


# ── Eval context ──────────────────────────────────────────────────────────────

_SAFE_BUILTINS = {"min": min, "max": max, "abs": abs, "int": int, "float": float,
                  "round": round, "bool": bool}


def _eval_context(runtime: ContractRuntime, event: Event) -> dict[str, Any]:
    ctx: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    ctx.update(runtime.fields)
    ctx.update(runtime.computed_cache)
    ctx.update(runtime.assets)
    ctx.update(event.details)
    ctx["event_type"] = event.event_type
    ctx["party"] = event.party
    if "days_late" in ctx:
        try:
            ctx["days_late"] = int(ctx["days_late"])
        except (TypeError, ValueError):
            pass
    return ctx


# ── Grace period lookup ───────────────────────────────────────────────────────

def _find_grace_days(fsm: FSMDefinition, effect_id: str) -> int | None:
    """Return grace period days if a GRACE_PERIOD constraint covers this effect."""
    for rule in fsm.rules + fsm.unmappable_rules:
        for c in rule.constraints:
            if c.type == ConstraintType.GRACE_PERIOD:
                if not c.scope or effect_id in c.scope:
                    return c.duration_days
    return None


# ── Aggregate cap tracking ────────────────────────────────────────────────────

def _find_aggregate_cap(
    fsm: FSMDefinition, effect_id: str
) -> tuple[str | None, Decimal | None]:
    """Return (constraint_id, cap_value) for any AGGREGATE_CONSTRAINT covering effect."""
    for rule in fsm.rules + fsm.unmappable_rules:
        for c in rule.constraints:
            if c.type == ConstraintType.AGGREGATE_CONSTRAINT:
                if not c.scope or effect_id in c.scope:
                    return c.id, c.value
    return None, None


# ── Effect dispatch ───────────────────────────────────────────────────────────

def _apply_effect(
    effect: dict,
    runtime: ContractRuntime,
    event: Event,
    fsm: FSMDefinition,
) -> None:
    etype = effect.get("type", "")
    ctx = _eval_context(runtime, event)

    if etype == EffectType.PAYMENT.value or etype == "PAYMENT":
        formula = effect.get("payment_formula")
        amount = Decimal("0")
        if formula:
            try:
                raw_val = eval(formula, {}, ctx)  # noqa: S307
                amount = Decimal(str(raw_val))
            except Exception:
                amount = Decimal("0")

        # Inline cap on the effect itself
        if effect.get("cap") is not None:
            cap_val = Decimal(str(effect["cap"]))
            if amount > cap_val:
                amount = cap_val

        # Grace period: skip payment if within grace window
        effect_id = effect.get("id", "")
        grace_days = _find_grace_days(fsm, effect_id)
        if grace_days is not None:
            days_late = ctx.get("days_late", 0)
            try:
                days_late = int(days_late)
            except (TypeError, ValueError):
                days_late = 0
            if days_late <= grace_days:
                amount = Decimal("0")

        # Aggregate cap: clamp to remaining headroom
        agg_constraint_id, agg_cap = _find_aggregate_cap(fsm, effect_id)
        if agg_constraint_id is not None and agg_cap is not None:
            agg_key = f"agg_{agg_constraint_id}"
            already_accrued = runtime.assets.get(agg_key, Decimal("0"))
            headroom = agg_cap - already_accrued
            if headroom <= 0:
                amount = Decimal("0")
            elif amount > headroom:
                amount = headroom
            runtime.assets[agg_key] = already_accrued + amount

        payer = effect.get("payment_from", "unknown")
        payee = effect.get("payment_to", "unknown")
        key = f"{payer}_to_{payee}"
        runtime.assets[key] = runtime.assets.get(key, Decimal("0")) + amount
        if effect_id:
            runtime.computed_cache[effect_id] = amount

    elif etype in (EffectType.STATE_TRANSITION.value, "STATE_TRANSITION"):
        new_state = effect.get("new_state", runtime.state)
        runtime.state = new_state

    elif etype in (EffectType.RULE_ACTIVATION.value, "RULE_ACTIVATION"):
        target_id = effect.get("target_rule_id")
        new_state_str = effect.get("new_state", "ACTIVE")
        if target_id and target_id in runtime.rules:
            try:
                runtime.rules[target_id].activation = ActivationState(new_state_str)
            except ValueError:
                pass

    elif etype in (EffectType.RETROACTIVE_REVISION.value, "RETROACTIVE_REVISION"):
        runtime.computed_cache.clear()
        if "revised_field" in effect:
            runtime.fields[effect["revised_field"]] = effect.get("revised_value")

    elif etype in (EffectType.TERMINATION.value, "TERMINATION"):
        new_state = effect.get("new_state", "TERMINATED")
        runtime.state = new_state

    elif etype in (EffectType.CURE_WINDOW.value, "CURE_WINDOW"):
        duration_days = effect.get("duration_days")
        if duration_days:
            try:
                deadline = event.timestamp + timedelta(days=int(duration_days))
                runtime.pending_events.append(
                    ScheduledEvent(
                        trigger_date=deadline,
                        event=Event(deadline, "cure_deadline_passed", "system", {}),
                        condition=None,
                    )
                )
            except (TypeError, ValueError):
                pass

    else:
        # Log unhandled effect types to narrative
        desc = effect.get("description", etype)
        runtime.narrative.append(f"  [effect] {etype}: {desc}")


# ── State rule activation sync ────────────────────────────────────────────────

def _sync_active_rules(runtime: ContractRuntime, fsm: FSMDefinition) -> None:
    """Update rule activation states to match the current FSM state's active_rule_ids."""
    state_obj = next((s for s in fsm.states if s.id == runtime.state), None)
    if state_obj is None:
        return
    active_ids = set(state_obj.active_rule_ids)
    for rid, rule in runtime.rules.items():
        if rid in active_ids:
            if rule.activation == ActivationState.INACTIVE:
                rule.activation = ActivationState.ACTIVE
        else:
            if rule.activation == ActivationState.ACTIVE:
                rule.activation = ActivationState.INACTIVE


# ── Main execution function ───────────────────────────────────────────────────

def execute(
    runtime: ContractRuntime,
    events: list[Event],
    fsm: FSMDefinition,
    verbose: bool = False,
) -> ExecutionResult:
    """
    Replay a list of Events against the runtime, returning an ExecutionResult.
    """
    sorted_events = sorted(events, key=lambda e: e.timestamp)
    last_date: date | None = None

    for event in sorted_events:
        # Advance time: fire pending events whose trigger_date <= event.timestamp
        if last_date is not None:
            pending_to_fire = [
                pe for pe in runtime.pending_events
                if pe.trigger_date <= event.timestamp
            ]
            for pe in pending_to_fire:
                runtime.pending_events.remove(pe)
                # Recursively process the scheduled event (single-level)
                _process_single_event(pe.event, runtime, fsm, verbose)
        last_date = event.timestamp

        _process_single_event(event, runtime, fsm, verbose)

    # Build result
    financial_summary = {
        k: v for k, v in runtime.assets.items() if isinstance(v, Decimal)
    }
    rule_states = {rid: r.activation.value for rid, r in runtime.rules.items()}

    return ExecutionResult(
        scenario_name="",  # caller fills this in
        final_state=runtime.state,
        financial_summary=financial_summary,
        rule_states=rule_states,
        event_log=runtime.event_log,
        computed_values=dict(runtime.computed_cache),
        narrative=list(runtime.narrative),
    )


def _process_single_event(
    event: Event,
    runtime: ContractRuntime,
    fsm: FSMDefinition,
    verbose: bool,
) -> None:
    """Find and apply a matching transition for one event."""
    ctx = _eval_context(runtime, event)

    matching = []
    for tr in fsm.transitions:
        if runtime.state not in tr.from_states:
            continue
        if tr.event_type != event.event_type:
            continue
        if tr.guard:
            try:
                passes = eval(tr.guard, {}, ctx)  # noqa: S307
            except Exception:
                passes = False
            if not passes:
                continue
        matching.append(tr)

    if not matching:
        msg = (
            f"{event.timestamp} [no transition] event={event.event_type!r} "
            f"in state={runtime.state!r}"
        )
        runtime.narrative.append(msg)
        if verbose:
            print(f"  [executor] {msg}")
        return

    # Take first matching transition
    tr = matching[0]
    old_state = runtime.state

    # Apply effects first (before moving state, so guards see old state)
    for effect in tr.effects:
        _apply_effect(effect, runtime, event, fsm)

    # Move to new state (may already have been moved by a TERMINATION/STATE_TRANSITION effect)
    if runtime.state == old_state:
        runtime.state = tr.to_state

    # Sync rule activation to new state
    _sync_active_rules(runtime, fsm)

    runtime.event_log.append(event)

    msg = (
        f"{event.timestamp} [{event.event_type}] "
        f"{old_state} → {runtime.state}"
    )
    runtime.narrative.append(msg)
    if verbose:
        print(f"  [executor] {msg}")
