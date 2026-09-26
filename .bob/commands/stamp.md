# /stamp — Verified Stamp Card Audit

## Description

Audits the current working branch against the full repository AST, executes sandbox repro tests
for every detected violation, and generates a **Verified Stamp Card** directly in the Bob chat
panel.

Invoke `/stamp fix` to automatically apply an atomic patch for a detected violation and re-run the
repro test in a loop until it passes.

---

## Usage

```
/stamp           # Audit current branch; output Stamp Card preview
/stamp fix       # Audit + apply atomic patch for each violation + re-run repro until passing
```

---

## Behaviour

### `/stamp`

1. **Index the repository AST**
   Call `src/engine/context_indexer.py` to build a `RepositoryIndex` rooted at the workspace.

2. **Collect the diff**
   Run `git diff main...HEAD` (or `git diff origin/main...HEAD` in CI) to obtain the unified diff
   for the current branch.

3. **Audit invariants**
   Pass the diff and the `RepositoryIndex` to `InvariantOracle().audit_diff()` from
   `src/engine/invariant_oracle.py`. Each returned `InvariantViolation` carries:
   - `rule_id`, `severity` (CRITICAL / WARNING)
   - `file_path`, `line_range`, `offending_code`
   - `ast_context` (enclosing function/class qualname)
   - `message`, `remediation`

4. **Sandbox repro verification**
   For every CRITICAL or WARNING violation:
   - Synthesise a repro test via `src/generator/repro_synthesizer.synthesize_repro_test()`.
   - Execute it in the sandbox via `src/generator/sandbox_runner.run_repro_in_sandbox()`.
   - Mark the violation `sandbox_confirmed: true` only if the sandbox exit code is non-zero
     (i.e., the violation reproduces). Violations that do not reproduce are marked
     `sandbox_confirmed: false` and excluded from the final verdict.

5. **Render Stamp Card**
   Emit a single structured Stamp Card in the Bob chat panel. No free-form prose outside
   the card schema. Schema:

   ```
   ╔══════════════════════════════════════════════════════╗
   ║  STAMPBOB VERIFIED STAMP CARD                        ║
   ╠══════════════════════════════════════════════════════╣
   ║  PR / Branch   : <branch-name>                       ║
   ║  Commit        : <short-sha>                         ║
   ║  Verdict       : APPROVED | REJECTED                 ║
   ║  Confidence    : <0.00–1.00>                         ║
   ╠══════════════════════════════════════════════════════╣
   ║  VIOLATIONS (<n> confirmed)                          ║
   ║  ─────────────────────────────────────────────────   ║
   ║  [1] <rule_id> · <severity>                          ║
   ║      File      : <file_path>:<start>-<end>           ║
   ║      Context   : <ast_context>                       ║
   ║      Code      : <offending_code>                    ║
   ║      Message   : <message>                           ║
   ║      Fix       : <remediation>                       ║
   ║      Sandbox   : CONFIRMED | UNVERIFIED              ║
   ╠══════════════════════════════════════════════════════╣
   ║  Gate: precision >= 0.92, hallucination <= 0.05      ║
   ╚══════════════════════════════════════════════════════╝
   ```

   - Verdict is **REJECTED** if any `sandbox_confirmed: true` CRITICAL violation exists.
   - Verdict is **APPROVED** if all violations are either absent or `sandbox_confirmed: false`.

---

### `/stamp fix`

Extends the audit above with an automated remediation loop:

1. Run the full `/stamp` audit.
2. For each `sandbox_confirmed: true` violation (in CRITICAL-first order):
   a. Apply the **atomic patch** described in `violation.remediation` as a minimal targeted edit.
   b. Re-run `InvariantOracle().audit_diff()` on the patched diff.
   c. Re-run the sandbox repro test.
   d. Repeat (a)–(c) until the violation no longer reproduces (max 3 iterations).
   e. If still failing after 3 iterations, emit a PATCH_FAILED entry and halt.
3. Output an updated Stamp Card reflecting the post-patch state.

---

## Constraints

- **Read-only by default.** `/stamp` (without `fix`) never modifies files, commits, or pushes.
- **No inline comment spam.** Only violations backed by an `InvariantViolation` object from
  `InvariantOracle.audit_diff()` are surfaced. Speculative observations are silently dropped.
- **Sandbox gating.** Findings not confirmed by a passing sandbox repro test are excluded from
  the verdict and clearly labelled UNVERIFIED in the card.
- **Active rules:** `GOROUTINE_LEAK_RULE`, `UNBUFFERED_CHANNEL_RULE`, `NIL_CHECK_BOUNDARY_RULE`,
  `OTEL_SEMANTIC_RULE` — see `src/engine/rules.py`.
