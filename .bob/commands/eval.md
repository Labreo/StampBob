# /eval — Offline Evaluation Benchmark

## Description

Executes the **50-PR offline evaluation benchmark harness** (`src/eval/benchmark_harness.py`)
against the golden fixture dataset at `fixtures/golden_prs/` and displays real-time
precision/recall curves inside the Bob IDE chat panel.

---

## Usage

```
/eval                             # Run with default threshold (0.92) and sandbox enabled
/eval --threshold 0.95            # Override minimum precision gate
/eval --no-sandbox                # Static analysis only — skip sandbox repro execution
/eval --dataset-path <path>       # Use an alternate golden fixture directory
```

---

## Behaviour

1. **Load golden fixtures**
   Call `src/eval/dataset_loader.load_golden_dataset()` to load all 50 `GoldenPR` JSON files
   from `fixtures/golden_prs/`. Each fixture contains a PR diff, ground-truth verdict
   (APPROVED/REJECTED), and expected violation annotations.

2. **Evaluate each fixture**
   For each fixture, call `src/eval/benchmark_harness.evaluate_fixture()`:
   - Build an `InvariantOracle` and run `audit_diff()` against the fixture diff.
   - Optionally confirm violations in the sandbox (`src/generator/sandbox_runner`).
   - Derive the predicted verdict and compare against ground truth.
   - Accumulate an `EvalResult` (verdict_correct, detected violations, error if any).

   Emit a live progress line per fixture directly in the Bob chat panel:
   ```
   [ 1/50] ✓ pr-0001  (goroutine_leak)
   [ 2/50] ✓ pr-0002  (nil_check_boundary)
   [ 3/50] ✗ pr-0003  (otel_semantic)  ← FP: unexpected violation
   ...
   ```

3. **Compute metrics**
   Call `src/eval/metrics.compute_metrics()` to derive:
   - **Precision** — fraction of REJECTED predictions that were correct
   - **Recall** — fraction of true REJECTED ground truths that were detected
   - **F1** — harmonic mean of precision and recall
   - **Hallucination rate** — fraction of predictions involving a spurious violation
   - **Per-category breakdown** — metrics split by rule category

4. **Real-time precision/recall curves**
   After all fixtures complete, render a precision/recall curve inside the Bob chat panel using
   the `create_chart` tool (LineChart). Two series:
   - `Precision@k` — rolling precision as fixtures are processed in order
   - `Recall@k` — rolling recall as fixtures are processed in order

   Example chart configuration:
   ```json
   {
     "type": "LineChart",
     "title": "Eval — Precision / Recall Curve (50 PRs)",
     "xAxisData": ["pr-0001", "pr-0002", ..., "pr-0050"],
     "series": [
       { "name": "Precision@k", "data": [...] },
       { "name": "Recall@k",    "data": [...] }
     ]
   }
   ```

5. **Summary table**
   Emit the ASCII summary table produced by
   `src/eval/benchmark_harness.format_summary_table()` verbatim in the chat panel.

6. **CI gate result**
   Report one of:
   - **PASSED** — precision >= threshold (default 0.92) AND hallucination rate <= 0.05
   - **FAILED** — one or both gates missed; display which gate failed and by how much

---

## CI Gate Thresholds

| Gate               | Default  | Flag to override        |
|--------------------|----------|-------------------------|
| Min precision      | 0.92     | `--threshold <float>`   |
| Max hallucination  | 0.05     | *(not configurable)*    |

---

## Output contract

- All output is emitted inside the Bob IDE chat panel.
- The precision/recall LineChart is rendered inline via `create_chart`.
- The ASCII summary table is rendered in a fenced code block.
- No files are written or modified.
- Exit behaviour mirrors `benchmark_harness.run_offline_eval()`: the command is considered
  failed (non-zero) if `summary.passed_ci_gate` is `False`.
