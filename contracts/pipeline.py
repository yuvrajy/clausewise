"""
pipeline.py — Orchestrates all six steps of the Executable Contracts pipeline.

Steps:
  1. extract_rules        (extractor.py)
  2. assemble_fsm         (assembler.py)
  3. validate_and_report  (validator.py)  ← retry loop
  4. generate_scenarios   (scenario_gen.py)
  5. execute per scenario (executor.py)
  6. decompile            (decompiler.py)
"""
from __future__ import annotations
import time

from .extractor    import extract_rules
from .assembler    import assemble_fsm
from .validator    import validate_and_report
from .scenario_gen import generate_scenarios
from .executor     import create_runtime, execute
from .decompiler   import decompile
from .schema       import PipelineResult


def _ck(msg: str) -> None:
    """Checkpoint print — always flushes so output appears live."""
    print(msg, flush=True)


def run_pipeline(
    contract_markdown: str,
    contract_id: str = "contract",
    verbose: bool = False,
    max_assembler_retries: int = 2,
) -> PipelineResult:
    """
    Run the full pipeline on a contract markdown string.
    Returns a PipelineResult with the FSM, execution results, English text,
    and any remaining validation issues.
    """
    t0 = time.time()

    def elapsed() -> str:
        return f"{time.time() - t0:.1f}s"

    # ── Step 1: Extract rules ─────────────────────────────────────────────────
    _ck(f"\n[1/6] Extracting rules from contract ({len(contract_markdown)} chars) …")
    t1 = time.time()
    rules, params = extract_rules(contract_markdown, verbose=verbose)
    mappable = sum(1 for r in rules if r.mappable)
    _ck(f"  ✓ {len(rules)} rules extracted ({mappable} mappable, "
        f"{len(rules)-mappable} unmappable) [{time.time()-t1:.1f}s]")
    _ck(f"  Parties: {params.parties}")
    _ck(f"  Amounts: {dict(list(params.amounts.items())[:4])}")

    # ── Step 2: Assemble FSM ──────────────────────────────────────────────────
    _ck(f"\n[2/6] Assembling FSM from {mappable} mappable rules …")
    t2 = time.time()
    fsm = assemble_fsm(rules, params, contract_id=contract_id, verbose=verbose)
    _ck(f"  ✓ FSM built: {len(fsm.states)} states, {len(fsm.transitions)} transitions "
        f"[{time.time()-t2:.1f}s]")
    for s in fsm.states:
        tag = " [TERMINAL]" if s.terminal else ""
        _ck(f"    state: {s.id}{tag}")

    # ── Step 3: Validate FSM ──────────────────────────────────────────────────
    _ck(f"\n[3/6] Validating FSM …")
    ok, issues = validate_and_report(fsm)
    if ok:
        _ck("  ✓ Validation passed")
    else:
        _ck(f"  ✗ {len(issues)} validation issues:")
        for iss in issues:
            _ck(f"    - {iss}")

    retries_left = max_assembler_retries
    while not ok and retries_left > 0:
        retries_left -= 1
        _ck(f"\n  Retrying assembly with feedback ({retries_left} retries left) …")
        t_retry = time.time()
        prior_ids = [s.id for s in fsm.states]
        fsm = assemble_fsm(
            rules, params,
            contract_id=contract_id,
            feedback=issues,
            prior_state_ids=prior_ids,
            verbose=verbose,
        )
        ok, issues = validate_and_report(fsm)
        if ok:
            _ck(f"  ✓ Validation passed after retry [{time.time()-t_retry:.1f}s]")
        else:
            _ck(f"  ✗ Still {len(issues)} issues after retry [{time.time()-t_retry:.1f}s]")
            for iss in issues:
                _ck(f"    - {iss}")

    # ── Step 4: Generate scenarios ────────────────────────────────────────────
    _ck(f"\n[4/6] Generating scenarios …")
    t4 = time.time()
    scenarios = generate_scenarios(contract_markdown, fsm, verbose=verbose)
    _ck(f"  ✓ {len(scenarios)} scenarios generated [{time.time()-t4:.1f}s]:")
    for name, evts in scenarios.items():
        _ck(f"    {name}: {len(evts)} events")

    # ── Step 5: Execute scenarios ─────────────────────────────────────────────
    _ck(f"\n[5/6] Executing scenarios …")
    results = {}
    for name, events in scenarios.items():
        _ck(f"  → {name} ({len(events)} events) …", )
        t5 = time.time()
        rt = create_runtime(fsm)
        result = execute(rt, events, fsm, verbose=verbose)
        result.scenario_name = name
        results[name] = result
        fin = result.financial_summary
        money = {k: f"${v:,.2f}" for k, v in fin.items() if v != 0}
        _ck(f"    final state: {result.final_state}  |  "
            f"financials: {money if money else '(none)'} [{time.time()-t5:.1f}s]")

    # ── Step 6: Decompile ─────────────────────────────────────────────────────
    _ck(f"\n[6/6] Decompiling FSM to English …")
    english = decompile(fsm)
    _ck(f"  ✓ {len(english)} chars generated")

    _ck(f"\n✓ Pipeline complete in {elapsed()}")

    return PipelineResult(
        fsm=fsm,
        execution_results=results,
        english=english,
        validation_issues=issues,
    )
