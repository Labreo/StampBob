#!/usr/bin/env python3
"""
generate_golden_fixtures.py — StampBob Offline Evaluation Benchmark Generator.

Generates 50 golden PR fixtures across 5 categories (10 PRs each) into
fixtures/golden_prs/{id}.json.  Each fixture carries a realistic Go or Python
diff, the expected verdict, the expected rules that should fire, and a golden
reproduction snippet for round-trip validation of the repro synthesizer.

Usage
-----
    python scripts/generate_golden_fixtures.py [--out-dir PATH]

The default output directory is ``fixtures/golden_prs`` relative to the
project root (the directory that contains this script's parent).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# Data model (mirrors GoldenPR in dataset_loader.py)
# ---------------------------------------------------------------------------

@dataclass
class FixturePR:
    pr_id: str
    category: str
    title: str
    diff_content: str
    expected_verdict: str          # "APPROVED" | "REJECTED"
    expected_rules: List[str]      # rule IDs that must fire; [] for CLEAN
    golden_repro_snippet: str      # minimal runnable test demonstrating the bug


# ---------------------------------------------------------------------------
# Category 1 — CLEAN (10 PRs, Expected: APPROVED, 0 findings)
# ---------------------------------------------------------------------------

_CLEAN: List[FixturePR] = [
    FixturePR(
        pr_id="PR-01",
        category="CLEAN",
        title="Refactor HTTP handler to use structured logging",
        diff_content="""\
diff --git a/internal/api/handler.go b/internal/api/handler.go
--- a/internal/api/handler.go
+++ b/internal/api/handler.go
@@ -12,7 +12,9 @@ func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
-\tlog.Printf("request: %s %s", r.Method, r.URL.Path)
+\th.logger.Info("request received",
+\t\t"method", r.Method,
+\t\t"path", r.URL.Path,
+\t)
 }
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-02",
        category="CLEAN",
        title="Add pagination support to ListUsers endpoint",
        diff_content="""\
diff --git a/internal/service/user.go b/internal/service/user.go
--- a/internal/service/user.go
+++ b/internal/service/user.go
@@ -20,6 +20,10 @@ func (s *UserService) ListUsers(ctx context.Context, req ListRequest) ([]User, e
+\tif req.PageSize <= 0 {
+\t\treq.PageSize = 20
+\t}
+\toffset := req.Page * req.PageSize
 \trows, err := s.db.QueryContext(ctx, listUsersSQL, req.PageSize, offset)
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-03",
        category="CLEAN",
        title="Replace magic numbers with named constants in scheduler",
        diff_content="""\
diff --git a/pkg/scheduler/scheduler.go b/pkg/scheduler/scheduler.go
--- a/pkg/scheduler/scheduler.go
+++ b/pkg/scheduler/scheduler.go
@@ -5,0 +5,5 @@
+const (
+\tdefaultWorkers    = 8
+\tdefaultQueueDepth = 256
+\tdefaultRetryDelay = 5 * time.Second
+)
@@ -18,3 +23,3 @@ func New() *Scheduler {
-\ts.workers = 8
-\ts.queue = make(chan Job, 256)
+\ts.workers = defaultWorkers
+\ts.queue = make(chan Job, defaultQueueDepth)
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-04",
        category="CLEAN",
        title="Add unit tests for config parser edge cases",
        diff_content="""\
diff --git a/pkg/config/parser_test.go b/pkg/config/parser_test.go
--- /dev/null
+++ b/pkg/config/parser_test.go
@@ -0,0 +1,28 @@
+package config_test
+
+import (
+\t"testing"
+\t"github.com/example/app/pkg/config"
+)
+
+func TestParseEmptyFile(t *testing.T) {
+\t_, err := config.ParseFile("")
+\tif err == nil {
+\t\tt.Fatal("expected error for empty path")
+\t}
+}
+
+func TestParseValidConfig(t *testing.T) {
+\tcfg, err := config.ParseFile("testdata/valid.yaml")
+\tif err != nil {
+\t\tt.Fatalf("unexpected error: %v", err)
+\t}
+\tif cfg.Port != 8080 {
+\t\tt.Errorf("expected port 8080, got %d", cfg.Port)
+\t}
+}
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-05",
        category="CLEAN",
        title="Extract database retry logic into helper with context propagation",
        diff_content="""\
diff --git a/internal/db/retry.go b/internal/db/retry.go
--- /dev/null
+++ b/internal/db/retry.go
@@ -0,0 +1,22 @@
+package db
+
+import (
+\t"context"
+\t"time"
+)
+
+func WithRetry(ctx context.Context, attempts int, fn func() error) error {
+\tvar err error
+\tfor i := 0; i < attempts; i++ {
+\t\tif err = fn(); err == nil {
+\t\t\treturn nil
+\t\t}
+\t\tselect {
+\t\tcase <-ctx.Done():
+\t\t\treturn ctx.Err()
+\t\tcase <-time.After(time.Duration(i+1) * 100 * time.Millisecond):
+\t\t}
+\t}
+\treturn err
+}
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-06",
        category="CLEAN",
        title="Upgrade TLS minimum version to 1.3 in server config",
        diff_content="""\
diff --git a/internal/server/tls.go b/internal/server/tls.go
--- a/internal/server/tls.go
+++ b/internal/server/tls.go
@@ -8,2 +8,2 @@ func tlsConfig() *tls.Config {
-\tMinVersion: tls.VersionTLS12,
+\tMinVersion: tls.VersionTLS13,
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-07",
        category="CLEAN",
        title="Add graceful shutdown with context cancellation to gRPC server",
        diff_content="""\
diff --git a/cmd/server/main.go b/cmd/server/main.go
--- a/cmd/server/main.go
+++ b/cmd/server/main.go
@@ -22,0 +22,12 @@
+\tctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
+\tdefer cancel()
+
+\tgo func() {
+\t\t<-ctx.Done()
+\t\tgrpcServer.GracefulStop()
+\t}()
+
+\tif err := grpcServer.Serve(lis); err != nil {
+\t\tlog.Fatalf("serve: %v", err)
+\t}
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-08",
        category="CLEAN",
        title="Rename internal metric field to follow snake_case convention",
        diff_content="""\
diff --git a/internal/metrics/collector.go b/internal/metrics/collector.go
--- a/internal/metrics/collector.go
+++ b/internal/metrics/collector.go
@@ -14,4 +14,4 @@ type Collector struct {
-\tRequestCount  int64
-\tErrorCount    int64
+\trequest_count  int64
+\terror_count    int64
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-09",
        category="CLEAN",
        title="Add input validation for CreateOrder API request",
        diff_content="""\
diff --git a/internal/api/order.go b/internal/api/order.go
--- a/internal/api/order.go
+++ b/internal/api/order.go
@@ -10,0 +10,8 @@ func (h *OrderHandler) Create(w http.ResponseWriter, r *http.Request) {
+\tif req.Quantity <= 0 {
+\t\thttp.Error(w, "quantity must be positive", http.StatusBadRequest)
+\t\treturn
+\t}
+\tif req.ProductID == "" {
+\t\thttp.Error(w, "product_id is required", http.StatusBadRequest)
+\t\treturn
+\t}
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
    FixturePR(
        pr_id="PR-10",
        category="CLEAN",
        title="Switch from sync.Mutex to RWMutex for read-heavy cache",
        diff_content="""\
diff --git a/pkg/cache/cache.go b/pkg/cache/cache.go
--- a/pkg/cache/cache.go
+++ b/pkg/cache/cache.go
@@ -8,2 +8,2 @@ type Cache struct {
-\tmu   sync.Mutex
+\tmu   sync.RWMutex
@@ -18,2 +18,2 @@ func (c *Cache) Get(key string) (interface{}, bool) {
-\tc.mu.Lock()
-\tdefer c.mu.Unlock()
+\tc.mu.RLock()
+\tdefer c.mu.RUnlock()
""",
        expected_verdict="APPROVED",
        expected_rules=[],
        golden_repro_snippet="// No defect — APPROVED",
    ),
]


# ---------------------------------------------------------------------------
# Category 2 — GOROUTINE_LEAK (10 PRs, Expected: REJECTED)
# ---------------------------------------------------------------------------

_GOROUTINE_LEAK: List[FixturePR] = [
    FixturePR(
        pr_id="PR-11",
        category="GOROUTINE_LEAK",
        title="Add async background metric collector",
        diff_content="""\
diff --git a/pkg/worker.go b/pkg/worker.go
--- a/pkg/worker.go
+++ b/pkg/worker.go
@@ -14,0 +14,7 @@ func StartMetricCollector(cfg Config) {
+\tgo func() {
+\t\tfor {
+\t\t\tcollectMetrics(cfg.Endpoint)
+\t\t\ttime.Sleep(cfg.Interval)
+\t\t}
+\t}()
+}
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tStartMetricCollector(Config{Interval: time.Hour, Endpoint: "http://x"})
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline {
\t\tt.Fatal("goroutine leak not reproduced")
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-12",
        category="GOROUTINE_LEAK",
        title="Spawn per-request background audit logger",
        diff_content="""\
diff --git a/internal/audit/logger.go b/internal/audit/logger.go
--- a/internal/audit/logger.go
+++ b/internal/audit/logger.go
@@ -9,0 +9,6 @@ func (l *Logger) LogAsync(event AuditEvent) {
+\tgo func() {
+\t\tl.store.Save(event)
+\t}()
+}
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tl := &Logger{store: &blockingStore{}}
\tfor i := 0; i < 5; i++ { l.LogAsync(AuditEvent{}) }
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("goroutines leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-13",
        category="GOROUTINE_LEAK",
        title="Add fire-and-forget cache warm-up goroutine",
        diff_content="""\
diff --git a/pkg/cache/warmer.go b/pkg/cache/warmer.go
--- /dev/null
+++ b/pkg/cache/warmer.go
@@ -0,0 +1,10 @@
+package cache
+
+func WarmUp(keys []string, loader func(string) interface{}) {
+\tgo func() {
+\t\tfor _, k := range keys {
+\t\t\tloader(k)
+\t\t}
+\t}()
+}
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tnever := make(chan struct{})
\tWarmUp([]string{"a","b"}, func(k string) interface{} { <-never; return nil })
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("goroutine not leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-14",
        category="GOROUTINE_LEAK",
        title="Add background event subscriber without lifecycle management",
        diff_content="""\
diff --git a/internal/events/subscriber.go b/internal/events/subscriber.go
--- a/internal/events/subscriber.go
+++ b/internal/events/subscriber.go
@@ -7,0 +7,8 @@ func Subscribe(broker EventBroker, handler Handler) {
+\tgo func() {
+\t\tfor msg := range broker.Messages() {
+\t\t\thandler.Handle(msg)
+\t\t}
+\t}()
+}
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tb := NewInfiniteTestBroker()
\tSubscribe(b, NoopHandler{})
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("goroutine leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-15",
        category="GOROUTINE_LEAK",
        title="Introduce periodic health-ping goroutine in client pool",
        diff_content="""\
diff --git a/pkg/pool/client_pool.go b/pkg/pool/client_pool.go
--- a/pkg/pool/client_pool.go
+++ b/pkg/pool/client_pool.go
@@ -22,0 +22,9 @@ func (p *Pool) Start() {
+\tfor _, c := range p.clients {
+\t\tclient := c
+\t\tgo func() {
+\t\t\tfor {
+\t\t\t\tclient.Ping()
+\t\t\t\ttime.Sleep(30 * time.Second)
+\t\t\t}
+\t\t}()
+\t}
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tp := &Pool{clients: makeTestClients(3)}
\tp.Start()
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("goroutines not leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-16",
        category="GOROUTINE_LEAK",
        title="Add streaming response handler with uncancellable goroutine",
        diff_content="""\
diff --git a/internal/api/stream.go b/internal/api/stream.go
--- a/internal/api/stream.go
+++ b/internal/api/stream.go
@@ -11,0 +11,7 @@ func StreamHandler(w http.ResponseWriter, r *http.Request) {
+\tgo func() {
+\t\tfor chunk := range source {
+\t\t\tw.Write(chunk)
+\t\t}
+\t}()
+\tw.WriteHeader(http.StatusOK)
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tsource := make(chan []byte) // never closed
\tStreamHandler(httptest.NewRecorder(), httptest.NewRequest("GET", "/", nil))
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("goroutine leaked on open channel") }
}""",
    ),
    FixturePR(
        pr_id="PR-17",
        category="GOROUTINE_LEAK",
        title="Add rate-limiter background token refill without stop channel",
        diff_content="""\
diff --git a/pkg/ratelimit/limiter.go b/pkg/ratelimit/limiter.go
--- a/pkg/ratelimit/limiter.go
+++ b/pkg/ratelimit/limiter.go
@@ -18,0 +18,8 @@ func NewLimiter(rate int) *Limiter {
+\tgo func() {
+\t\tticker := time.NewTicker(time.Second)
+\t\tfor range ticker.C {
+\t\t\tl.refill(rate)
+\t\t}
+\t}()
+\treturn l
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\t_ = NewLimiter(10)
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("ticker goroutine leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-18",
        category="GOROUTINE_LEAK",
        title="Queue processor spawns goroutine without WaitGroup tracking",
        diff_content="""\
diff --git a/internal/queue/processor.go b/internal/queue/processor.go
--- a/internal/queue/processor.go
+++ b/internal/queue/processor.go
@@ -12,0 +12,6 @@ func (p *Processor) Dispatch(job Job) {
+\tgo func() {
+\t\tp.execute(job)
+\t}()
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tp := &Processor{execute: func(j Job) { time.Sleep(time.Hour) }}
\tp.Dispatch(Job{})
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("dispatch goroutine leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-19",
        category="GOROUTINE_LEAK",
        title="Add notification sender goroutine without stop signal",
        diff_content="""\
diff --git a/internal/notify/sender.go b/internal/notify/sender.go
--- a/internal/notify/sender.go
+++ b/internal/notify/sender.go
@@ -9,0 +9,7 @@ func (s *Sender) RunAsync() {
+\tgo func() {
+\t\tfor {
+\t\t\ts.flush()
+\t\t\ttime.Sleep(s.interval)
+\t\t}
+\t}()
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\ts := &Sender{interval: time.Hour}
\ts.RunAsync()
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("sender goroutine leaked") }
}""",
    ),
    FixturePR(
        pr_id="PR-20",
        category="GOROUTINE_LEAK",
        title="Add webhook retry loop goroutine without context",
        diff_content="""\
diff --git a/internal/webhook/retrier.go b/internal/webhook/retrier.go
--- a/internal/webhook/retrier.go
+++ b/internal/webhook/retrier.go
@@ -8,0 +8,10 @@ func (r *Retrier) RetryAsync(payload []byte) {
+\tgo func() {
+\t\tfor attempt := 0; attempt < r.maxAttempts; attempt++ {
+\t\t\tif err := r.send(payload); err == nil {
+\t\t\t\treturn
+\t\t\t}
+\t\t\ttime.Sleep(r.backoff)
+\t\t}
+\t}()
""",
        expected_verdict="REJECTED",
        expected_rules=["goroutine-leak"],
        golden_repro_snippet="""\
func TestReproLeak(t *testing.T) {
\tbaseline := liveGoroutines()
\tr := &Retrier{maxAttempts: 100, backoff: time.Hour, send: func([]byte) error { return errors.New("fail") }}
\tr.RetryAsync([]byte("ping"))
\ttime.Sleep(50 * time.Millisecond)
\tif liveGoroutines() <= baseline { t.Fatal("retry goroutine leaked") }
}""",
    ),
]


# ---------------------------------------------------------------------------
# Category 3 — UNBUFFERED_CHANNEL (10 PRs, Expected: REJECTED)
# ---------------------------------------------------------------------------

_UNBUFFERED_CHANNEL: List[FixturePR] = [
    FixturePR(
        pr_id="PR-21",
        category="UNBUFFERED_CHANNEL",
        title="Add parallel download manager with per-item channels",
        diff_content="""\
diff --git a/pkg/downloader/manager.go b/pkg/downloader/manager.go
--- a/pkg/downloader/manager.go
+++ b/pkg/downloader/manager.go
@@ -10,0 +10,8 @@ func (m *Manager) DownloadAll(urls []string) {
+\tfor _, u := range urls {
+\t\tch := make(chan Result)
+\t\tgo func(url string) {
+\t\t\tch <- m.fetch(url)
+\t\t}(u)
+\t}
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tdone := make(chan struct{})
\tch := make(chan Result) // no buffer
\tgo func() { ch <- Result{}; close(done) }()
\tselect {
\tcase <-done: t.Fatal("expected blocking send")
\tcase <-time.After(200 * time.Millisecond): // deadlock confirmed
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-22",
        category="UNBUFFERED_CHANNEL",
        title="Worker pool dispatcher creates per-task unbuffered result channels",
        diff_content="""\
diff --git a/internal/pool/dispatcher.go b/internal/pool/dispatcher.go
--- a/internal/pool/dispatcher.go
+++ b/internal/pool/dispatcher.go
@@ -8,0 +8,6 @@ func Dispatch(tasks []Task) []chan error {
+\tresults := make([]chan error, len(tasks))
+\tfor i, t := range tasks {
+\t\tresults[i] = make(chan error)
+\t\tgo execute(t, results[i])
+\t}
+\treturn results
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tresults := Dispatch(make([]Task, 3))
\tdone := make(chan struct{})
\tgo func() { <-results[0]; close(done) }()
\tselect {
\tcase <-done:
\tcase <-time.After(200 * time.Millisecond):
\t\tt.Log("DEADLOCK REPRODUCED: unbuffered send blocked")
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-23",
        category="UNBUFFERED_CHANNEL",
        title="Fan-out processor allocates one channel per message in range loop",
        diff_content="""\
diff --git a/internal/fanout/processor.go b/internal/fanout/processor.go
--- a/internal/fanout/processor.go
+++ b/internal/fanout/processor.go
@@ -6,0 +6,7 @@ func FanOut(msgs []Message, workers int) {
+\tfor _, msg := range msgs {
+\t\tch := make(chan Message)
+\t\tfor w := 0; w < workers; w++ {
+\t\t\tgo process(ch)
+\t\t}
+\t\tch <- msg
+\t}
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tch := make(chan Message)
\tdone := make(chan struct{})
\tgo func() { ch <- Message{}; close(done) }()
\tselect {
\tcase <-done: t.Fatal("expected blocking")
\tcase <-time.After(200 * time.Millisecond):
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-24",
        category="UNBUFFERED_CHANNEL",
        title="Add scatter-gather pipeline with unbuffered aggregation channels",
        diff_content="""\
diff --git a/pkg/pipeline/scatter.go b/pkg/pipeline/scatter.go
--- a/pkg/pipeline/scatter.go
+++ b/pkg/pipeline/scatter.go
@@ -9,0 +9,7 @@ func Scatter(input []int) []chan int {
+\tout := make([]chan int, len(input))
+\tfor i, v := range input {
+\t\tout[i] = make(chan int)
+\t\tval := v
+\t\tgo func() { out[i] <- val * 2 }()
+\t}
+\treturn out
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tchans := Scatter([]int{1, 2, 3})
\tdone := make(chan struct{})
\tgo func() { <-chans[0]; close(done) }()
\tselect {
\tcase <-done:
\tcase <-time.After(200 * time.Millisecond):
\t\tt.Log("DEADLOCK REPRODUCED")
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-25",
        category="UNBUFFERED_CHANNEL",
        title="Image resizer creates per-image unbuffered output channel in loop",
        diff_content="""\
diff --git a/pkg/imaging/resizer.go b/pkg/imaging/resizer.go
--- a/pkg/imaging/resizer.go
+++ b/pkg/imaging/resizer.go
@@ -7,0 +7,7 @@ func ResizeAll(images []Image) []chan Image {
+\tresults := make([]chan Image, len(images))
+\tfor i, img := range images {
+\t\tresults[i] = make(chan Image)
+\t\tgo func(src Image) { results[i] <- resize(src) }(img)
+\t}
+\treturn results
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tch := make(chan Image)
\tdone := make(chan struct{})
\tgo func() { ch <- Image{}; close(done) }()
\tselect {
\tcase <-done: t.Fatal("should have blocked")
\tcase <-time.After(200 * time.Millisecond):
\t\tt.Log("unbuffered channel deadlock confirmed")
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-26",
        category="UNBUFFERED_CHANNEL",
        title="Add batch-export loop with per-batch unbuffered ack channels",
        diff_content="""\
diff --git a/internal/exporter/batch.go b/internal/exporter/batch.go
--- a/internal/exporter/batch.go
+++ b/internal/exporter/batch.go
@@ -11,0 +11,7 @@ func ExportBatches(batches []Batch) {
+\tfor _, b := range batches {
+\t\tack := make(chan struct{})
+\t\tgo func(batch Batch) {
+\t\t\tsend(batch)
+\t\t\tack <- struct{}{}
+\t\t}(b)
+\t}
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tack := make(chan struct{})
\tdone := make(chan struct{})
\tgo func() { ack <- struct{}{}; close(done) }()
\tselect {
\tcase <-done: t.Fatal("expected blocking ack")
\tcase <-time.After(200 * time.Millisecond):
\t\tt.Log("DEADLOCK REPRODUCED: unbuffered ack blocked")
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-27",
        category="UNBUFFERED_CHANNEL",
        title="Add concurrent health checker with per-host unbuffered result channel",
        diff_content="""\
diff --git a/internal/health/checker.go b/internal/health/checker.go
--- a/internal/health/checker.go
+++ b/internal/health/checker.go
@@ -8,0 +8,7 @@ func CheckAll(hosts []string) []bool {
+\tresults := make([]chan bool, len(hosts))
+\tfor i, h := range hosts {
+\t\tresults[i] = make(chan bool)
+\t\tgo func(host string) { results[i] <- ping(host) }(h)
+\t}
+\t// caller must drain — but nobody will
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tch := make(chan bool)
\tdone := make(chan struct{})
\tgo func() { ch <- true; close(done) }()
\tselect {
\tcase <-done: t.Fatal("should have blocked")
\tcase <-time.After(200 * time.Millisecond):
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-28",
        category="UNBUFFERED_CHANNEL",
        title="Add report generator with per-section unbuffered channel in range",
        diff_content="""\
diff --git a/pkg/report/generator.go b/pkg/report/generator.go
--- a/pkg/report/generator.go
+++ b/pkg/report/generator.go
@@ -7,0 +7,7 @@ func GenerateSections(sections []Section) []string {
+\toutputs := make([]chan string, len(sections))
+\tfor i, s := range sections {
+\t\toutputs[i] = make(chan string)
+\t\tgo func(sec Section) { outputs[i] <- render(sec) }(s)
+\t}
+\treturn nil // forgot to drain
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tch := make(chan string)
\tdone := make(chan struct{})
\tgo func() { ch <- "section"; close(done) }()
\tselect {
\tcase <-done: t.Fatal("expected deadlock")
\tcase <-time.After(200 * time.Millisecond):
\t\tt.Log("unbuffered channel send confirmed blocked")
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-29",
        category="UNBUFFERED_CHANNEL",
        title="Subscription manager allocates per-subscriber unbuffered event channel",
        diff_content="""\
diff --git a/internal/pubsub/manager.go b/internal/pubsub/manager.go
--- a/internal/pubsub/manager.go
+++ b/internal/pubsub/manager.go
@@ -10,0 +10,6 @@ func (m *Manager) Subscribe(topics []string) []chan Event {
+\tchans := make([]chan Event, len(topics))
+\tfor i, t := range topics {
+\t\tchans[i] = make(chan Event)
+\t\tm.register(t, chans[i])
+\t}
+\treturn chans
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tch := make(chan Event)
\tdone := make(chan struct{})
\tgo func() { ch <- Event{}; close(done) }()
\tselect {
\tcase <-done: t.Fatal("expected block on unbuffered channel")
\tcase <-time.After(200 * time.Millisecond):
\t}
}""",
    ),
    FixturePR(
        pr_id="PR-30",
        category="UNBUFFERED_CHANNEL",
        title="Data pipeline stage creates per-record unbuffered output channel",
        diff_content="""\
diff --git a/pkg/pipeline/stage.go b/pkg/pipeline/stage.go
--- a/pkg/pipeline/stage.go
+++ b/pkg/pipeline/stage.go
@@ -8,0 +8,7 @@ func ProcessRecords(records []Record) {
+\tfor _, r := range records {
+\t\toutCh := make(chan Record)
+\t\trecord := r
+\t\tgo func() {
+\t\t\toutCh <- transform(record)
+\t\t}()
+\t}
""",
        expected_verdict="REJECTED",
        expected_rules=["unbuffered-channel-in-loop"],
        golden_repro_snippet="""\
func TestReproUnbufferedDeadlock(t *testing.T) {
\tch := make(chan Record)
\tdone := make(chan struct{})
\tgo func() { ch <- Record{}; close(done) }()
\tselect {
\tcase <-done: t.Fatal("expected blocking send")
\tcase <-time.After(200 * time.Millisecond):
\t\tt.Log("unbuffered channel deadlock confirmed")
\t}
}""",
    ),
]


# ---------------------------------------------------------------------------
# Category 4 — NIL_DEREF (10 PRs, Expected: REJECTED)
# ---------------------------------------------------------------------------

_NIL_DEREF: List[FixturePR] = [
    FixturePR(
        pr_id="PR-31",
        category="NIL_DEREF",
        title="Add user profile loader without nil guard on DB result",
        diff_content="""\
diff --git a/internal/service/profile.go b/internal/service/profile.go
--- a/internal/service/profile.go
+++ b/internal/service/profile.go
@@ -10,0 +10,5 @@ func (s *Service) GetProfile(ctx context.Context, userID string) (*Profile, error) {
+\tuser, _ := s.db.FindUser(ctx, userID)
+\t// Missing nil check — user may be nil when not found
+\treturn &Profile{Name: user.Name, Email: user.Email}, nil
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar user *User // nil
\t\t_ = user.Name // panics
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
\tt.Logf("NIL DEREFERENCE REPRODUCED: %v", recovered)
}""",
    ),
    FixturePR(
        pr_id="PR-32",
        category="NIL_DEREF",
        title="Add config loader that dereferences optional nested struct",
        diff_content="""\
diff --git a/pkg/config/loader.go b/pkg/config/loader.go
--- a/pkg/config/loader.go
+++ b/pkg/config/loader.go
@@ -14,0 +14,4 @@ func Load(path string) (*AppConfig, error) {
+\tcfg, _ := parse(path)
+\t// cfg.Database may be nil when section is absent
+\treturn cfg, fmt.Errorf("invalid dsn: %s", cfg.Database.DSN)
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar cfg *AppConfig
\t\t_ = cfg.Database.DSN
\t}()
\tif recovered == nil { t.Fatal("expected panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-33",
        category="NIL_DEREF",
        title="Cache miss handler accesses Value field without nil guard",
        diff_content="""\
diff --git a/pkg/cache/accessor.go b/pkg/cache/accessor.go
--- a/pkg/cache/accessor.go
+++ b/pkg/cache/accessor.go
@@ -8,0 +8,4 @@ func (c *Cache) MustGet(key string) interface{} {
+\tentry := c.lookup(key) // returns nil on miss
+\t// No nil guard — panics on cache miss
+\treturn entry.Value
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar entry *Entry
\t\t_ = entry.Value
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-34",
        category="NIL_DEREF",
        title="Order service reads shipping address fields without nil check",
        diff_content="""\
diff --git a/internal/order/service.go b/internal/order/service.go
--- a/internal/order/service.go
+++ b/internal/order/service.go
@@ -16,0 +16,5 @@ func (s *OrderService) Process(order *Order) error {
+\taddr := order.ShippingAddress // may be nil for digital goods
+\t// Missing: if addr == nil { return ErrNoAddress }
+\tif addr.City == "" {
+\t\treturn ErrInvalidAddress
+\t}
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar addr *Address
\t\t_ = addr.City
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-35",
        category="NIL_DEREF",
        title="Metrics reporter accesses optional label map without nil check",
        diff_content="""\
diff --git a/internal/metrics/reporter.go b/internal/metrics/reporter.go
--- a/internal/metrics/reporter.go
+++ b/internal/metrics/reporter.go
@@ -9,0 +9,4 @@ func (r *Reporter) Emit(m *Metric) {
+\t// Labels may be nil if metric was constructed without labels
+\tfor k, v := range m.Labels {
+\t\tr.sink.Tag(k, v)
+\t}
+\tr.sink.Count(m.Name, m.Labels["env"]) // panics when Labels is nil
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar m *Metric
\t\t_ = m.Labels["env"]
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-36",
        category="NIL_DEREF",
        title="gRPC interceptor reads request metadata without nil guard",
        diff_content="""\
diff --git a/internal/grpc/interceptor.go b/internal/grpc/interceptor.go
--- a/internal/grpc/interceptor.go
+++ b/internal/grpc/interceptor.go
@@ -12,0 +12,5 @@ func AuthInterceptor(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
+\tmd, _ := metadata.FromIncomingContext(ctx)
+\t// md may be nil — no nil guard
+\ttoken := md["authorization"][0]
+\tif !validateToken(token) {
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar md metadata.MD // nil
\t\t_ = md["authorization"][0]
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-37",
        category="NIL_DEREF",
        title="Plugin loader calls methods on optionally-registered plugin",
        diff_content="""\
diff --git a/pkg/plugin/loader.go b/pkg/plugin/loader.go
--- a/pkg/plugin/loader.go
+++ b/pkg/plugin/loader.go
@@ -9,0 +9,4 @@ func (l *Loader) Run(name string) error {
+\tp := l.registry[name] // returns nil (zero value) if not registered
+\t// Missing: if p == nil { return ErrNotFound }
+\treturn p.Execute()
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar p Plugin // nil interface
\t\t_ = p.Execute()
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-38",
        category="NIL_DEREF",
        title="Payment processor dereferences optional discount pointer",
        diff_content="""\
diff --git a/internal/payment/processor.go b/internal/payment/processor.go
--- a/internal/payment/processor.go
+++ b/internal/payment/processor.go
@@ -13,0 +13,4 @@ func (p *Processor) Total(cart *Cart) float64 {
+\t// cart.Discount is nil when no promo code applied
+\tdiscountPct := cart.Discount.Percentage
+\treturn cart.Subtotal * (1 - discountPct/100)
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar d *Discount
\t\t_ = d.Percentage
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-39",
        category="NIL_DEREF",
        title="Session middleware reads user object from context without nil check",
        diff_content="""\
diff --git a/internal/middleware/session.go b/internal/middleware/session.go
--- a/internal/middleware/session.go
+++ b/internal/middleware/session.go
@@ -10,0 +10,5 @@ func SessionMiddleware(next http.Handler) http.Handler {
+\treturn http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
+\t\tuser := r.Context().Value(userKey).(*User) // panics if key absent (nil assertion)
+\t\tlog.Printf("user: %s", user.Email)
+\t\tnext.ServeHTTP(w, r)
+\t})
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar u *User
\t\t_ = u.Email
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
    FixturePR(
        pr_id="PR-40",
        category="NIL_DEREF",
        title="Trace exporter dereferences optional span parent without guard",
        diff_content="""\
diff --git a/internal/trace/exporter.go b/internal/trace/exporter.go
--- a/internal/trace/exporter.go
+++ b/internal/trace/exporter.go
@@ -9,0 +9,4 @@ func (e *Exporter) Export(span *Span) {
+\t// Parent is nil for root spans
+\tparentID := span.Parent.TraceID
+\te.sink.Record(parentID, span.Name)
""",
        expected_verdict="REJECTED",
        expected_rules=["nil-check-boundary"],
        golden_repro_snippet="""\
func TestReproNilDeref(t *testing.T) {
\tvar recovered interface{}
\tfunc() {
\t\tdefer func() { recovered = recover() }()
\t\tvar parent *SpanContext
\t\t_ = parent.TraceID
\t}()
\tif recovered == nil { t.Fatal("expected nil dereference panic") }
}""",
    ),
]


# ---------------------------------------------------------------------------
# Category 5 — OTEL_OMISSION (10 PRs, Expected: REJECTED)
# ---------------------------------------------------------------------------

_OTEL_OMISSION: List[FixturePR] = [
    FixturePR(
        pr_id="PR-41",
        category="OTEL_OMISSION",
        title="Add gRPC ProductService.GetProduct endpoint without rpc.* span attributes",
        diff_content="""\
diff --git a/internal/rpc/product_handler.go b/internal/rpc/product_handler.go
--- a/internal/rpc/product_handler.go
+++ b/internal/rpc/product_handler.go
@@ -9,0 +9,10 @@ func (h *Handler) GetProduct(ctx context.Context, req *pb.GetProductRequest) (*pb.Product, error) {
+\tctx, span := tracer.Start(ctx, "ProductService.GetProduct")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("custom.product_id", req.ProductId),
+\t)
+\tp, err := h.store.Get(ctx, req.ProductId)
+\tif err != nil {
+\t\treturn nil, err
+\t}
+\treturn p.ToProto(), nil
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("GetProduct") as span:
        span.set_attribute("custom.product_id", "p-123")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    assert not any(k.startswith(("rpc.", "server.")) for k in attrs), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-42",
        category="OTEL_OMISSION",
        title="Add REST checkout endpoint with ad-hoc span attribute keys",
        diff_content="""\
diff --git a/internal/api/checkout.go b/internal/api/checkout.go
--- a/internal/api/checkout.go
+++ b/internal/api/checkout.go
@@ -12,0 +12,8 @@ func (h *CheckoutHandler) Checkout(w http.ResponseWriter, r *http.Request) {
+\tctx, span := tracer.Start(r.Context(), "checkout.process")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("checkout.cart_id", cartID),
+\t\tattribute.Int("checkout.item_count", itemCount),
+\t)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("checkout") as span:
        span.set_attribute("checkout.cart_id", "c-99")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    key = "checkout.cart_id"
    assert not any(key.startswith(p) for p in ["http.", "rpc.", "server."]), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-43",
        category="OTEL_OMISSION",
        title="Add AI inference RPC handler with missing gen_ai.* span attributes",
        diff_content="""\
diff --git a/internal/ai/infer_handler.go b/internal/ai/infer_handler.go
--- a/internal/ai/infer_handler.go
+++ b/internal/ai/infer_handler.go
@@ -10,0 +10,9 @@ func (h *InferHandler) Infer(ctx context.Context, req *InferRequest) (*InferResponse, error) {
+\tctx, span := tracer.Start(ctx, "ai.infer")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("model.name", req.ModelID),
+\t\tattribute.Int("inference.token_count", req.MaxTokens),
+\t)
+\treturn h.model.Run(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("ai.infer") as span:
        span.set_attribute("model.name", "gpt-4")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    assert "model.name" in attrs
    assert not attrs.get("model.name", "").startswith("gen_ai."), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-44",
        category="OTEL_OMISSION",
        title="Add billing RPC endpoint with custom non-semconv span keys",
        diff_content="""\
diff --git a/internal/billing/handler.go b/internal/billing/handler.go
--- a/internal/billing/handler.go
+++ b/internal/billing/handler.go
@@ -11,0 +11,8 @@ func (h *BillingHandler) Charge(ctx context.Context, req *ChargeRequest) (*Receipt, error) {
+\tctx, span := tracer.Start(ctx, "billing.charge")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("billing.customer_id", req.CustomerID),
+\t\tattribute.Float64("billing.amount_usd", req.AmountUSD),
+\t)
+\treturn h.processor.Charge(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("billing.charge") as span:
        span.set_attribute("billing.customer_id", "cust-42")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    approved = {"gen_ai","server","client","http","rpc","db"}
    assert not any("billing.customer_id".startswith(p) for p in approved), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-45",
        category="OTEL_OMISSION",
        title="Add file-upload endpoint with missing http.* semconv span attributes",
        diff_content="""\
diff --git a/internal/upload/handler.go b/internal/upload/handler.go
--- a/internal/upload/handler.go
+++ b/internal/upload/handler.go
@@ -10,0 +10,9 @@ func (h *UploadHandler) Upload(w http.ResponseWriter, r *http.Request) {
+\tctx, span := tracer.Start(r.Context(), "upload.file")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("upload.filename", filename),
+\t\tattribute.Int64("upload.bytes", fileSize),
+\t)
+\th.store.Save(ctx, filename, data)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("upload.file") as span:
        span.set_attribute("upload.filename", "doc.pdf")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    assert "upload.filename" in attrs
    assert not "upload.filename".startswith("http."), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-46",
        category="OTEL_OMISSION",
        title="Add search service RPC with proprietary span attribute namespace",
        diff_content="""\
diff --git a/internal/search/handler.go b/internal/search/handler.go
--- a/internal/search/handler.go
+++ b/internal/search/handler.go
@@ -10,0 +10,9 @@ func (h *SearchHandler) Query(ctx context.Context, req *SearchRequest) (*SearchResponse, error) {
+\tctx, span := tracer.Start(ctx, "search.query")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("search.query_text", req.Query),
+\t\tattribute.Int("search.result_count", req.MaxResults),
+\t)
+\treturn h.engine.Search(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("search.query") as span:
        span.set_attribute("search.query_text", "golang channels")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    approved_prefixes = {"gen_ai","server","client","http","rpc","db","messaging"}
    assert not any("search.query_text".startswith(p) for p in approved_prefixes), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-47",
        category="OTEL_OMISSION",
        title="Add notification dispatch RPC without required rpc.method span attribute",
        diff_content="""\
diff --git a/internal/notify/rpc_handler.go b/internal/notify/rpc_handler.go
--- a/internal/notify/rpc_handler.go
+++ b/internal/notify/rpc_handler.go
@@ -9,0 +9,9 @@ func (h *NotifyHandler) Send(ctx context.Context, req *SendRequest) (*SendResponse, error) {
+\tctx, span := tracer.Start(ctx, "notify.send")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("notification.channel", req.Channel),
+\t\tattribute.String("notification.template_id", req.TemplateID),
+\t)
+\treturn h.dispatcher.Send(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("notify.send") as span:
        span.set_attribute("notification.channel", "email")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    approved = {"messaging","rpc","server","http"}
    assert not any("notification.channel".startswith(p) for p in approved), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-48",
        category="OTEL_OMISSION",
        title="Add analytics event RPC endpoint with non-standard span keys",
        diff_content="""\
diff --git a/internal/analytics/handler.go b/internal/analytics/handler.go
--- a/internal/analytics/handler.go
+++ b/internal/analytics/handler.go
@@ -10,0 +10,9 @@ func (h *AnalyticsHandler) Track(ctx context.Context, req *TrackRequest) error {
+\tctx, span := tracer.Start(ctx, "analytics.track")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("analytics.event_name", req.EventName),
+\t\tattribute.String("analytics.user_segment", req.Segment),
+\t)
+\treturn h.sink.Emit(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("analytics.track") as span:
        span.set_attribute("analytics.event_name", "page_view")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    approved = {"gen_ai","server","client","http","rpc","db","faas"}
    assert not any("analytics.event_name".startswith(p) for p in approved), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-49",
        category="OTEL_OMISSION",
        title="Add recommendation engine RPC with missing rpc.service attribute",
        diff_content="""\
diff --git a/internal/recommend/handler.go b/internal/recommend/handler.go
--- a/internal/recommend/handler.go
+++ b/internal/recommend/handler.go
@@ -10,0 +10,9 @@ func (h *RecommendHandler) Suggest(ctx context.Context, req *SuggestRequest) (*SuggestResponse, error) {
+\tctx, span := tracer.Start(ctx, "recommend.suggest")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("recommend.user_id", req.UserID),
+\t\tattribute.Int("recommend.limit", req.Limit),
+\t)
+\treturn h.engine.Suggest(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("recommend.suggest") as span:
        span.set_attribute("recommend.user_id", "u-001")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    approved = {"gen_ai","server","client","http","rpc","db"}
    assert not any("recommend.user_id".startswith(p) for p in approved), "otel semconv violation confirmed"
""",
    ),
    FixturePR(
        pr_id="PR-50",
        category="OTEL_OMISSION",
        title="Add inventory sync RPC with ad-hoc span attribute namespace",
        diff_content="""\
diff --git a/internal/inventory/sync_handler.go b/internal/inventory/sync_handler.go
--- a/internal/inventory/sync_handler.go
+++ b/internal/inventory/sync_handler.go
@@ -10,0 +10,9 @@ func (h *SyncHandler) Sync(ctx context.Context, req *SyncRequest) (*SyncResponse, error) {
+\tctx, span := tracer.Start(ctx, "inventory.sync")
+\tdefer span.End()
+\tspan.SetAttributes(
+\t\tattribute.String("inventory.warehouse_id", req.WarehouseID),
+\t\tattribute.Int("inventory.sku_count", req.SKUCount),
+\t)
+\treturn h.syncer.Sync(ctx, req)
""",
        expected_verdict="REJECTED",
        expected_rules=["otel-semantic-convention"],
        golden_repro_snippet="""\
def test_repro_otel_omission():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("repro")
    with tracer.start_as_current_span("inventory.sync") as span:
        span.set_attribute("inventory.warehouse_id", "wh-7")
    attrs = dict(exporter.get_finished_spans()[0].attributes)
    approved = {"gen_ai","server","client","http","rpc","db","messaging","k8s"}
    assert not any("inventory.warehouse_id".startswith(p) for p in approved), "otel semconv violation confirmed"
""",
    ),
]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

ALL_FIXTURES = _CLEAN + _GOROUTINE_LEAK + _UNBUFFERED_CHANNEL + _NIL_DEREF + _OTEL_OMISSION


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for fixture in ALL_FIXTURES:
        dest = out_dir / f"{fixture.pr_id.lower().replace('-', '_')}.json"
        payload = asdict(fixture)
        dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        counts[fixture.category] = counts.get(fixture.category, 0) + 1

    total = sum(counts.values())
    print(f"Generated {total} golden PR fixtures → {out_dir}")
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate golden PR benchmark fixtures.")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).parent.parent / "fixtures" / "golden_prs"),
        help="Output directory for fixture JSON files (default: fixtures/golden_prs)",
    )
    args = parser.parse_args()
    generate(Path(args.out_dir))


if __name__ == "__main__":
    main()
