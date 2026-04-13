"""
schema.py — All dataclasses for the executable contracts pipeline.

Four mappable primitives (user's scope):
  OBLIGATION  — party MUST do X by deadline
  PUNISHMENT  — money/consequence triggered by breach  (maps to Effect.PAYMENT + Rule.VIOLATION)
  DEADLINE    — fixed or computed date that fires a transition
  CONDITION   — boolean predicate guarding a transition

Everything else (PERMISSION, PROHIBITION, POWER, etc.) is extracted but
flagged as unmappable and stored for later handling.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from enum import Enum
from typing import Any, Optional


# ── Enumerations ─────────────────────────────────────────────────────────────

class RuleType(str, Enum):
    OBLIGATION     = "OBLIGATION"
    PROHIBITION    = "PROHIBITION"
    PERMISSION     = "PERMISSION"
    POWER          = "POWER"
    IMMUNITY       = "IMMUNITY"
    REPRESENTATION = "REPRESENTATION"
    WARRANTY       = "WARRANTY"
    COVENANT       = "COVENANT"

class ActivationState(str, Enum):
    INACTIVE   = "INACTIVE"
    ACTIVE     = "ACTIVE"
    FULFILLED  = "FULFILLED"
    VIOLATED   = "VIOLATED"

class TriggerType(str, Enum):
    DATE_TRIGGER         = "DATE_TRIGGER"
    EVENT_TRIGGER        = "EVENT_TRIGGER"
    CONDITION_PRECEDENT  = "CONDITION_PRECEDENT"
    CONDITION_SUBSEQUENT = "CONDITION_SUBSEQUENT"
    THRESHOLD_TRIGGER    = "THRESHOLD_TRIGGER"
    NOTICE_TRIGGER       = "NOTICE_TRIGGER"
    TEMPORAL_TRIGGER     = "TEMPORAL_TRIGGER"
    ABSENCE_TRIGGER      = "ABSENCE_TRIGGER"

class EffectType(str, Enum):
    PAYMENT             = "PAYMENT"
    ASSET_TRANSFER      = "ASSET_TRANSFER"
    STATE_TRANSITION    = "STATE_TRANSITION"
    RULE_ACTIVATION     = "RULE_ACTIVATION"
    CURE_WINDOW         = "CURE_WINDOW"
    ACCELERATION        = "ACCELERATION"
    FORFEITURE          = "FORFEITURE"
    TERMINATION         = "TERMINATION"
    RETROACTIVE_REVISION = "RETROACTIVE_REVISION"

class ConstraintType(str, Enum):
    CAP                  = "CAP"
    FLOOR                = "FLOOR"
    GRACE_PERIOD         = "GRACE_PERIOD"
    AGGREGATE_CONSTRAINT = "AGGREGATE_CONSTRAINT"
    PRO_RATA             = "PRO_RATA"
    CARVE_OUT            = "CARVE_OUT"
    CONTROLLING_ITEM     = "CONTROLLING_ITEM"


# ── Core building blocks ──────────────────────────────────────────────────────

@dataclass
class Trigger:
    id: str
    type: TriggerType
    condition: str
    reference_date: Optional[str] = None   # field name the duration is relative to
    duration: Optional[str] = None          # e.g. "90 days"
    duration_days: Optional[int] = None     # parsed integer days
    threshold_field: Optional[str] = None
    threshold_value: Optional[str] = None
    threshold_operator: Optional[str] = None  # gt | lt | gte | lte | eq


@dataclass
class Effect:
    id: str
    type: EffectType
    description: str
    target_rule_id: Optional[str] = None
    payment_formula: Optional[str] = None   # Python expr e.g. "days_late * 500"
    payment_from: Optional[str] = None
    payment_to: Optional[str] = None
    cap: Optional[Decimal] = None
    new_state: Optional[str] = None         # for STATE_TRANSITION effects


@dataclass
class Constraint:
    id: str
    type: ConstraintType
    value: Optional[Decimal] = None
    duration: Optional[str] = None
    duration_days: Optional[int] = None
    scope: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class Rule:
    id: str
    type: RuleType
    party: str
    counterparty: str
    action: str
    activation: ActivationState
    triggers: list[Trigger]
    effects: list[Effect]
    constraints: list[Constraint]
    exceptions: list[str]
    expiry: Optional[Trigger]
    description: str
    mappable: bool = True           # False for non-four-primitive types
    unmappable_reason: Optional[str] = None


# ── Contract parameters (static, set at formation) ───────────────────────────

@dataclass
class ContractParams:
    parties: list[str]
    base_dates: dict[str, Optional[str]]    # name → ISO date string or None
    amounts: dict[str, str]                  # name → string (parsed to Decimal at runtime)
    rates: dict[str, str]
    durations: dict[str, int]               # name → days
    thresholds: dict[str, str]


# ── FSM definition (output of assembler) ─────────────────────────────────────

@dataclass
class FSMState:
    id: str
    description: str
    terminal: bool
    active_rule_ids: list[str] = field(default_factory=list)


@dataclass
class FSMTransition:
    id: str
    from_states: list[str]
    to_state: str
    event_type: str          # event_type from Event that triggers this
    guard: Optional[str]     # Python boolean expression evaluated in runtime ctx
    effects: list[dict]      # serialisable effect specs
    description: str = ""


@dataclass
class FSMDefinition:
    contract_id: str
    parties: list[str]
    states: list[FSMState]
    transitions: list[FSMTransition]
    initial_state: str
    rules: list[Rule]
    params: ContractParams
    unmappable_rules: list[Rule] = field(default_factory=list)


# ── Runtime (mutates during execution) ───────────────────────────────────────

@dataclass
class Event:
    timestamp: date
    event_type: str
    party: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduledEvent:
    trigger_date: date
    event: Event
    condition: Optional[str] = None


@dataclass
class ContractRuntime:
    state: str
    rules: dict[str, Rule]
    assets: dict[str, Decimal]
    fields: dict[str, Any]
    event_log: list[Event]
    pending_events: list[ScheduledEvent]
    computed_cache: dict[str, Any]
    narrative: list[str] = field(default_factory=list)


# ── Execution result ──────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    scenario_name: str
    final_state: str
    financial_summary: dict[str, Decimal]
    rule_states: dict[str, str]
    event_log: list[Event]
    computed_values: dict[str, Any]
    narrative: list[str]          # human-readable log of what happened


# ── Pipeline result ───────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    fsm: FSMDefinition
    execution_results: dict[str, ExecutionResult]
    english: str
    validation_issues: list[str]
