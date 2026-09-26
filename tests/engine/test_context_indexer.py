"""
Tests for src/engine/context_indexer.py
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from src.engine.context_indexer import (
    BlastRadius,
    CallEdge,
    CallGraph,
    FileSymbols,
    FunctionSignature,
    RepositoryIndex,
    TypeDeclaration,
    build_call_graph,
    build_repository_index,
    compute_blast_radius,
    parse_ast_symbols,
)


# ---------------------------------------------------------------------------
# Fixtures: write temporary source files
# ---------------------------------------------------------------------------

PYTHON_SOURCE = textwrap.dedent("""\
    import os
    import asyncio

    class Processor:
        def process(self, data: str) -> bool:
            return bool(data)

        def _internal(self):
            pass

    def run(items):
        t = asyncio.create_task(main())
        return t

    async def main():
        p = Processor()
        p.process("hello")
""")

GO_SOURCE = textwrap.dedent("""\
    package compute

    import (
        "context"
        "sync"
    )

    type Worker struct {
        mu sync.Mutex
    }

    type Scheduler interface {
        Schedule(ctx context.Context) error
    }

    func NewWorker() *Worker {
        return &Worker{}
    }

    func (w *Worker) Run(ctx context.Context) error {
        w.mu.Lock()
        defer w.mu.Unlock()
        go processItem(ctx)
        return nil
    }

    func processItem(ctx context.Context) {
        ctx, cancel := context.WithCancel(ctx)
        defer cancel()
        ch := make(chan int, 1)
        ch <- 1
        v := <-ch
        _ = v
    }
""")

PYTHON_CALLER_SOURCE = textwrap.dedent("""\
    from mymodule import run

    def trigger():
        run([1, 2, 3])
""")

INVALID_PYTHON = "def bad syntax((("


@pytest.fixture
def py_file(tmp_path: Path) -> Path:
    f = tmp_path / "mymodule.py"
    f.write_text(PYTHON_SOURCE)
    return f


@pytest.fixture
def go_file(tmp_path: Path) -> Path:
    f = tmp_path / "worker.go"
    f.write_text(GO_SOURCE)
    return f


@pytest.fixture
def caller_file(tmp_path: Path) -> Path:
    f = tmp_path / "caller.py"
    f.write_text(PYTHON_CALLER_SOURCE)
    return f


@pytest.fixture
def invalid_py_file(tmp_path: Path) -> Path:
    f = tmp_path / "bad.py"
    f.write_text(INVALID_PYTHON)
    return f


# ---------------------------------------------------------------------------
# parse_ast_symbols — Python
# ---------------------------------------------------------------------------

class TestParseAstSymbolsPython:
    def test_returns_file_symbols(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        assert isinstance(fs, FileSymbols)
        assert fs.language == "python"
        assert fs.parse_error is None

    def test_extracts_functions(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        names = {f.name for f in fs.functions}
        assert "run" in names
        assert "main" in names

    def test_extracts_class_methods(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        names = {f.name for f in fs.functions}
        assert "process" in names
        assert "_internal" in names

    def test_exported_flag(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        by_name = {f.name: f for f in fs.functions}
        assert by_name["run"].is_exported is True
        assert by_name["_internal"].is_exported is False

    def test_qualname_includes_module(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        qualnames = {f.qualname for f in fs.functions}
        assert any("mymodule" in q for q in qualnames)

    def test_method_receiver(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        by_name = {f.name: f for f in fs.functions}
        assert by_name["process"].is_method is True
        assert by_name["process"].receiver == "Processor"

    def test_extracts_class_declaration(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        names = {t.name for t in fs.types}
        assert "Processor" in names

    def test_class_kind(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        cls = next(t for t in fs.types if t.name == "Processor")
        assert cls.kind == "class"

    def test_imports_captured(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        assert "os" in fs.imports
        assert "asyncio" in fs.imports

    def test_concurrency_markers_asyncio(self, py_file):
        fs = parse_ast_symbols(str(py_file))
        kinds = {m.kind for m in fs.concurrency_markers}
        assert "goroutine" in kinds   # asyncio.create_task maps to "goroutine"

    def test_invalid_python_graceful(self, invalid_py_file):
        fs = parse_ast_symbols(str(invalid_py_file))
        assert fs.parse_error is not None
        assert "SyntaxError" in fs.parse_error

    def test_missing_file_graceful(self, tmp_path):
        fs = parse_ast_symbols(str(tmp_path / "nonexistent.py"))
        assert fs.parse_error is not None

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# hello")
        fs = parse_ast_symbols(str(f))
        assert fs.language == "unknown"
        assert fs.parse_error is not None


# ---------------------------------------------------------------------------
# parse_ast_symbols — Go
# ---------------------------------------------------------------------------

class TestParseAstSymbolsGo:
    def test_returns_file_symbols(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        assert isinstance(fs, FileSymbols)
        assert fs.language == "go"
        assert fs.parse_error is None

    def test_package_name(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        assert fs.package == "compute"

    def test_extracts_functions(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        names = {f.name for f in fs.functions}
        assert "NewWorker" in names
        assert "Run" in names
        assert "processItem" in names

    def test_exported_flag(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        by_name = {f.name: f for f in fs.functions}
        assert by_name["NewWorker"].is_exported is True
        assert by_name["processItem"].is_exported is False

    def test_method_receiver(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        by_name = {f.name: f for f in fs.functions}
        assert by_name["Run"].is_method is True
        assert by_name["Run"].receiver == "Worker"

    def test_extracts_struct_type(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        names = {t.name: t for t in fs.types}
        assert "Worker" in names
        assert names["Worker"].kind == "struct"

    def test_extracts_interface_type(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        names = {t.name: t for t in fs.types}
        assert "Scheduler" in names
        assert names["Scheduler"].kind == "interface"

    def test_imports_captured(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        assert "context" in fs.imports
        assert "sync" in fs.imports

    def test_goroutine_marker(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        kinds = {m.kind for m in fs.concurrency_markers}
        assert "goroutine" in kinds

    def test_mutex_markers(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        kinds = {m.kind for m in fs.concurrency_markers}
        assert "mutex_lock" in kinds
        assert "mutex_unlock" in kinds

    def test_channel_markers(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        kinds = {m.kind for m in fs.concurrency_markers}
        assert "channel_send" in kinds
        assert "channel_recv" in kinds

    def test_context_cancel_marker(self, go_file):
        fs = parse_ast_symbols(str(go_file))
        kinds = {m.kind for m in fs.concurrency_markers}
        assert "context_cancel" in kinds


# ---------------------------------------------------------------------------
# build_call_graph
# ---------------------------------------------------------------------------

class TestBuildCallGraph:
    def test_returns_call_graph(self, py_file):
        cg = build_call_graph([str(py_file)])
        assert isinstance(cg, CallGraph)

    def test_edges_present(self, py_file):
        cg = build_call_graph([str(py_file)])
        assert len(cg.edges) > 0

    def test_edge_structure(self, py_file):
        cg = build_call_graph([str(py_file)])
        edge = cg.edges[0]
        assert isinstance(edge, CallEdge)
        assert isinstance(edge.caller_qualname, str)
        assert isinstance(edge.callee_qualname, str)
        assert isinstance(edge.caller_line, int)

    def test_callee_file_resolved_for_local_calls(self, py_file):
        cg = build_call_graph([str(py_file)])
        # process is called from main — both are in the same file
        resolved = [e for e in cg.edges if e.callee_file is not None]
        assert len(resolved) > 0

    def test_indexes_populated(self, py_file):
        cg = build_call_graph([str(py_file)])
        # build_indexes is called inside build_call_graph
        assert isinstance(cg._callers_of, dict)
        assert isinstance(cg._callees_of, dict)

    def test_callers_of(self, py_file):
        cg = build_call_graph([str(py_file)])
        # At least one symbol should have callers
        all_callees = {e.callee_qualname for e in cg.edges}
        for sym in all_callees:
            callers = cg.callers_of(sym)
            assert isinstance(callers, list)

    def test_empty_files_list(self):
        cg = build_call_graph([])
        assert cg.edges == []

    def test_multi_file(self, py_file, go_file):
        cg = build_call_graph([str(py_file), str(go_file)])
        assert len(cg.edges) > 0

    def test_skips_parse_errors(self, invalid_py_file, py_file):
        cg = build_call_graph([str(invalid_py_file), str(py_file)])
        # Should not raise; invalid file is skipped
        assert isinstance(cg, CallGraph)


# ---------------------------------------------------------------------------
# compute_blast_radius
# ---------------------------------------------------------------------------

class TestComputeBlastRadius:
    def test_returns_blast_radius(self, py_file, caller_file):
        br = compute_blast_radius(
            changed_files=[str(py_file)],
            diff_symbols={"mymodule.run"},
            all_files=[str(py_file), str(caller_file)],
        )
        assert isinstance(br, BlastRadius)

    def test_changed_symbols_echoed(self, py_file):
        br = compute_blast_radius(
            changed_files=[str(py_file)],
            diff_symbols={"mymodule.run"},
            all_files=[str(py_file)],
        )
        assert "mymodule.run" in br.changed_symbols

    def test_empty_diff_symbols(self, py_file):
        br = compute_blast_radius(
            changed_files=[str(py_file)],
            diff_symbols=set(),
            all_files=[str(py_file)],
        )
        assert br.directly_impacted_files == set()
        assert br.broken_edges == []

    def test_packages_derived(self, py_file, caller_file):
        br = compute_blast_radius(
            changed_files=[str(py_file)],
            diff_symbols={"mymodule.run"},
            all_files=[str(py_file), str(caller_file)],
        )
        assert isinstance(br.impacted_packages, set)

    def test_accepts_prebuilt_call_graph(self, py_file):
        cg = build_call_graph([str(py_file)])
        br = compute_blast_radius(
            changed_files=[str(py_file)],
            diff_symbols={"mymodule.run"},
            call_graph=cg,
        )
        assert isinstance(br, BlastRadius)

    def test_broken_edges_type(self, py_file):
        cg = build_call_graph([str(py_file)])
        br = compute_blast_radius(
            changed_files=[str(py_file)],
            diff_symbols={"mymodule.run"},
            call_graph=cg,
        )
        assert all(isinstance(e, CallEdge) for e in br.broken_edges)


# ---------------------------------------------------------------------------
# RepositoryIndex / build_repository_index
# ---------------------------------------------------------------------------

class TestRepositoryIndex:
    def test_build_index(self, tmp_path):
        (tmp_path / "a.py").write_text(PYTHON_SOURCE)
        (tmp_path / "b.go").write_text(GO_SOURCE)
        idx = build_repository_index(str(tmp_path))
        assert isinstance(idx, RepositoryIndex)

    def test_all_files_populated(self, tmp_path):
        (tmp_path / "a.py").write_text(PYTHON_SOURCE)
        idx = build_repository_index(str(tmp_path))
        assert len(idx.all_files()) >= 1

    def test_file_symbols_populated(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text(PYTHON_SOURCE)
        idx = build_repository_index(str(tmp_path))
        assert str(f) in idx.file_symbols

    def test_package_files_mapping(self, tmp_path):
        (tmp_path / "a.py").write_text(PYTHON_SOURCE)
        idx = build_repository_index(str(tmp_path))
        assert len(idx.package_files) >= 1

    def test_symbol_def_lookup(self, tmp_path):
        (tmp_path / "mymodule.py").write_text(PYTHON_SOURCE)
        idx = build_repository_index(str(tmp_path))
        fp = idx.symbol_file("mymodule.run")
        assert fp is not None
        assert "mymodule.py" in fp

    def test_call_graph_populated(self, tmp_path):
        (tmp_path / "a.py").write_text(PYTHON_SOURCE)
        idx = build_repository_index(str(tmp_path))
        assert isinstance(idx.call_graph, CallGraph)

    def test_skips_vendor_and_hidden(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "secret.py").write_text("x = 1")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "lib.go").write_text(GO_SOURCE)
        (tmp_path / "main.py").write_text("x = 1")
        idx = build_repository_index(str(tmp_path))
        indexed = idx.all_files()
        # Check that none of the indexed files live *inside* .hidden or vendor
        # (use Path.parts to avoid false matches on the tmp_path itself).
        for f in indexed:
            parts = Path(f).parts
            assert ".hidden" not in parts, f"{f} should not be indexed"
            assert "vendor" not in parts, f"{f} should not be indexed"

    def test_empty_repo(self, tmp_path):
        idx = build_repository_index(str(tmp_path))
        assert idx.all_files() == []
