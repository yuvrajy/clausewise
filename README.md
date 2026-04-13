# Executable Contracts

Contracts as finite state machines — extracted by LLM, executed deterministically, decompiled to English.


## Quickstart

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
```

**Run any contract through the full pipeline:**

```bash
python -m contracts.demo your_contract.md
# writes output/your_contract/{fsm.json, english.txt, scenarios.txt, report.txt}
```

**Compile the FSM to a standalone Python module and run a scenario:**

```bash
python contract-fsm/compiler.py output/your_contract/fsm.json -o contract.py
python contract.py scenario.json
# writes execution_result.json, prints financial summary + execution log
```

**Visualize:**

```bash
open contract-fsm/visualizer.html
# paste fsm.json into box 1, execution_result.json into box 2
```

**Pre-computed output** (no API key needed to inspect): `output/ORBCOMM-*/` and `output/trap_orphan_voided_procedure/`.

**Use the FSM to ground an LLM.** Feed `fsm.json` as structured context to any LLM so it can answer questions about the contract with precision — which states exist, what triggers a penalty, what the caps are — without hallucinating from prose.

**Resumable runner for large contracts** (saves each step, picks up on re-run):

```bash
python3 run_orbcomm.py            # run / resume
python3 run_orbcomm.py --fresh    # start over
```


## How This Scores

| Criterion (weight) | What to look at |
|---|---|
| **Expressiveness** (25%) | ORBCOMM: 26 mappable rules with typed triggers, effects, constraints, caps, and payment formulas — plus 14 unmappable rules explicitly flagged with reasons, not silently dropped. See any `output/*/fsm.json`. |
| **Executability** (25%) | Real dollar amounts per scenario: `$204,000` on-time incentive, `$510,000` late penalty, `$680,000` holdback released pro-rata. See any `output/*/scenarios.txt`. |
| **Round-trip Fidelity** (25%) | Decompiler is pure Python, sorted iteration, fixed templates — no LLM. Run twice, diff `english.txt`. Identical. |
| **Generality** (15%) | Same code on a procurement contract (ORBCOMM: milestones, penalties, holdbacks) and a municipal infrastructure contract (trap/orphan: SAT testing, warranty, arbitration). Feed it any `.md`. |
| **Creativity** (10%) | FSM-SCG-inspired decomposition with validation feedback loop. Hohfeldian rule taxonomy. Two-pass extraction. Four adversarial trap contracts. Interactive browser visualizer. |


## Architecture

```
CONTRACT.md
    │
    ▼ LLM
[1] EXTRACTOR (two-pass)
    Pass A: free-prose inventory
    Pass B: structured JSON
    │
    ▼ LLM
[2] ASSEMBLER
    Rules → States + Transitions
    │
    ▼ pure Python
[3] VALIDATOR ──── fails ──► feedback to [2]
    │ passes
    ├──────────────────────────────────┐
    │                                  │
    ▼ LLM            pure Python ▼    pure Python ▼
[4] SCENARIO GEN   [5] EXECUTOR      [6] DECOMPILER
    │                   │                  │
    ▼                   ▼                  ▼
scenarios.txt    execution_result    english.txt
                                    (round-trip)
                 visualizer.html
```

**LLM boundary:** Steps 1, 2, 4 use Claude. Steps 3, 5, 6 are pure Python — deterministic, auditable, diff-safe.


## Trap Contracts

Four adversarial contracts in `contract-fsm/examples/` stress-test the pipeline — each encodes a structural trap a naive extraction would miss:

| Contract | Trap |
|---|---|
| `trap_orphan_voided_procedure.md` | Section 14.3 supersedes the Section 6.4 dispute procedure, making that FSM state an unreachable orphan. |
| `trap_cross_clause_cap.md` | Two penalty streams share a combined aggregate cap defined in a third clause — naive extraction applies it per-stream. |
| `trap_retroactive_recalculation.md` | Remediation time is added to the delivery date, so penalties computed at delivery must be retroactively revised. |
| `trap_selective_force_majeure.md` | Force majeure suspends late penalties but explicitly does not extend the early delivery incentive window — same event, opposite effects. |


## Design Choices

**Contracts as FSMs.** Accumulation (daily accruing penalties), cross-clause caps, retroactive corrections, and selective modifiers all break the if-then tree model. An FSM with variables handles all of them: states carry running totals, transitions fire on events, side effects compute money, constraints enforce globally.

**Decomposed LLM calls with validation feedback.** Instead of one call to "turn this contract into code," we use two: (1) extract rules (reading comprehension), then (2) assemble FSM (structural mapping). A programmatic validator checks reachability, dead ends, and formula syntax, then feeds errors back to the assembler for retry. This is the FSM-SCG pattern (Luo et al., 2025): direct generation succeeded 36.9% of the time; with the FSM step, 95.3%.

**Two-pass extraction.** Pass A is unconstrained free-prose inventory — captures nuances before any schema category constrains what the model notices. Pass B fills the structured JSON grounded in Pass A. FlowFSM (Wael et al., 2025) reports 83–88% precision with a similar multi-stage chain.

**Strict LLM boundary.** Validation, execution, and decompilation are deterministic by design, not convention. The executable → English path is LLM-free.

**Hohfeldian rule taxonomy.** Extracted rules are typed as obligation, prohibition, permission, power, immunity, representation, warranty, or covenant. The first four map to computable state changes; the rest are extracted but flagged as unmappable with reasons — never silently dropped.


## Limitations

- Temporal reasoning is approximate: deadlines are integer day counts, no business days or time zones.
- Complex cross-reference chains (e.g., "as defined in Section 4.2(b)(iii)") are not resolved systematically.
- Single document snapshot — does not layer amendments on a base agreement.
- FSM validator checks structural properties, not semantic correctness.


## Project Structure

```
contracts/
  schema.py          Dataclass definitions
  extractor.py       Step 1: LLM two-pass extraction
  assembler.py       Step 2: LLM FSM assembly
  validator.py       Step 3: pure-Python validation
  scenario_gen.py    Step 4: LLM scenario generation
  executor.py        Step 5: pure-Python execution engine
  decompiler.py      Step 6: deterministic English decompiler
  pipeline.py        Orchestrator
  demo.py            CLI entry point

contract-fsm/
  compiler.py        FSM JSON → standalone Python module
  decompiler.py      Standalone decompiler CLI
  visualizer.html    Interactive browser demo
  examples/          Sample contracts + westex FSM + scenario JSONs

output/
  ORBCOMM.../          Pre-computed: fsm.json, english.txt, scenarios.txt, report.txt
  trap_orphan.../      Pre-computed: fsm.json, english.txt, scenarios.txt, report.txt
```


## Requirements

```
Python 3.10+
anthropic >= 0.40.0
```

No other dependencies. Executor, validator, decompiler, and compiler use only the standard library.


## References

- H. Luo et al. **Guiding LLM-based Smart Contract Generation with Finite State Machine.** IJCAI 2025. — FSM decomposition with validation feedback: 36.9% → 95.3% success.
- S. Crafa, C. Laneve, G. Sartor. **Pacta sunt servanda: Legal contracts in Stipula.** 2021. arXiv:2110.11069.
- F. Wael et al. **An Agentic Flow for FSM Extraction using Prompt Chaining.** 2025. arXiv:2507.11222. — 83–88% precision.
- D. Merigoux et al. **Catala: A Programming Language for the Law.** 2021. arXiv:2103.03198.
