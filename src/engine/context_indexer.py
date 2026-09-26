"""
context_indexer.py — StampBob repository-level static analysis engine.

Indexes Go and Python source files across the workspace to expose:
  - Symbol tables (functions, classes/structs, interfaces)
  - Cross-package call graphs
  - Blast-radius computation for changed public signatures
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FunctionSignature:
    """Represents a parsed function or method signature."""

    name: str
    qualname: str          # e.g. "MyClass.my_method" or "pkg.MyFunc"
    file_path: str
    line: int
    is_exported: bool       # uppercase first char (Go) / no leading _ (Python)
    params: List[str]       # parameter names, types omitted for language-agnostic repr
    returns: List[str]
    is_method: bool = False
    receiver: Optional[str] = None   # Go receiver type


@dataclass
class TypeDeclaration:
    """Struct (Go) / Class (Python) or interface declaration."""

    name: str
    file_path: str
    line: int
    is_exported: bool
    kind: str              # "class", "struct", "interface"
    bases: List[str] = field(default_factory=list)    # Python bases / Go embedded
    methods: List[str] = field(default_factory=list)  # method names


@dataclass
class ConcurrencyMarker:
    """Records usage of concurrency primitives found in a file."""

    file_path: str
    line: int
    kind: str   # "goroutine", "channel_send", "channel_recv", "mutex_lock",
                # "mutex_unlock", "context_cancel", "context_deadline",
                # "context_timeout", "waitgroup"
    detail: str = ""


@dataclass
class FileSymbols:
    """All extracted symbols from a single source file."""

    file_path: str
    language: str                          # "python" or "go"
    package: str = ""                      # Go package name; Python module name
    functions: List[FunctionSignature] = field(default_factory=list)
    types: List[TypeDeclaration] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    concurrency_markers: List[ConcurrencyMarker] = field(default_factory=list)
    parse_error: Optional[str] = None


@dataclass
class CallEdge:
    """A directed edge in the call graph."""

    caller_qualname: str   # "pkg.Func" or "module.Class.method"
    caller_file: str
    caller_line: int
    callee_qualname: str   # may be unresolved ("unknown.SomeFunc")
    callee_file: Optional[str] = None  # None when callee is external / unresolved


@dataclass
class CallGraph:
    """Directed call graph spanning all indexed files."""

    edges: List[CallEdge] = field(default_factory=list)

    # Derived indexes built by build_call_graph; populated lazily.
    _callers_of: Dict[str, List[CallEdge]] = field(default_factory=dict, repr=False)
    _callees_of: Dict[str, List[CallEdge]] = field(default_factory=dict, repr=False)

    def build_indexes(self) -> None:
        """Populate reverse-lookup indexes from edge list."""
        self._callers_of.clear()
        self._callees_of.clear()
        for edge in self.edges:
            self._callees_of.setdefault(edge.caller_qualname, []).append(edge)
            self._callers_of.setdefault(edge.callee_qualname, []).append(edge)

    def callers_of(self, qualname: str) -> List[CallEdge]:
        """Return all edges that call *qualname*."""
        return self._callers_of.get(qualname, [])

    def callees_of(self, qualname: str) -> List[CallEdge]:
        """Return all edges called from *qualname*."""
        return self._callees_of.get(qualname, [])


@dataclass
class BlastRadius:
    """Impact surface for a set of changed symbols."""

    changed_symbols: Set[str]
    directly_impacted_files: Set[str]          # files that directly call changed symbols
    transitively_impacted_files: Set[str]      # transitive downstream files
    impacted_packages: Set[str]                # unique package names of impacted files
    broken_edges: List[CallEdge]               # edges whose callee was changed


@dataclass
class RepositoryIndex:
    """
    Full repository index: maps file paths to their extracted symbol tables,
    the cross-file call graph, and package → file membership.
    """

    root: str                                         # absolute path to repo root
    file_symbols: Dict[str, FileSymbols] = field(default_factory=dict)
    call_graph: CallGraph = field(default_factory=CallGraph)
    package_files: Dict[str, List[str]] = field(default_factory=dict)  # pkg → [files]

    # Symbol → defining file quick-lookup
    _symbol_defs: Dict[str, str] = field(default_factory=dict, repr=False)

    def all_files(self) -> List[str]:
        return list(self.file_symbols.keys())

    def symbol_file(self, qualname: str) -> Optional[str]:
        return self._symbol_defs.get(qualname)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _detect_language(file_path: str) -> Optional[str]:
    ext = Path(file_path).suffix.lower()
    return {"py": "python", ".go": "go", ".py": "python"}.get(ext) or \
           {"go": "go"}.get(ext[1:] if ext.startswith(".") else ext)


def _is_exported_go(name: str) -> bool:
    return bool(name) and name[0].isupper()


def _is_exported_python(name: str) -> bool:
    return bool(name) and not name.startswith("_")


# ---------------------------------------------------------------------------
# Python AST parser
# ---------------------------------------------------------------------------

class _PythonVisitor(ast.NodeVisitor):
    """Single-pass AST visitor that extracts symbols from a Python source file."""

    def __init__(self, file_path: str, module_name: str) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.functions: List[FunctionSignature] = []
        self.types: List[TypeDeclaration] = []
        self.imports: List[str] = []
        self.concurrency_markers: List[ConcurrencyMarker] = []
        self._class_stack: List[str] = []

    # --- imports ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)

    # --- classes ---

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [ast.unparse(b) for b in node.bases]
        td = TypeDeclaration(
            name=node.name,
            file_path=self.file_path,
            line=node.lineno,
            is_exported=_is_exported_python(node.name),
            kind="class",
            bases=bases,
        )
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()
        td.methods = [
            f.name for f in self.functions
            if f.is_method and f.receiver == node.name
        ]
        self.types.append(td)

    # --- functions & methods ---

    def _extract_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        params = [a.arg for a in node.args.args]
        returns: List[str] = []
        if node.returns is not None:
            returns = [ast.unparse(node.returns)]

        in_class = bool(self._class_stack)
        receiver = self._class_stack[-1] if in_class else None
        qualname_parts = [self.module_name] + self._class_stack + [node.name]
        qualname = ".".join(filter(None, qualname_parts))

        sig = FunctionSignature(
            name=node.name,
            qualname=qualname,
            file_path=self.file_path,
            line=node.lineno,
            is_exported=_is_exported_python(node.name),
            params=params,
            returns=returns,
            is_method=in_class,
            receiver=receiver,
        )
        self.functions.append(sig)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._extract_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._extract_func(node)

    # --- call-site concurrency heuristics ---

    _CONCURRENCY_CALLS: Dict[str, str] = {
        "threading.Lock": "mutex_lock",
        "asyncio.Lock": "mutex_lock",
        "threading.Event": "mutex_lock",
        "asyncio.create_task": "goroutine",
        "asyncio.ensure_future": "goroutine",
        "ThreadPoolExecutor": "goroutine",
        "ProcessPoolExecutor": "goroutine",
    }

    def visit_Call(self, node: ast.Call) -> None:
        try:
            func_str = ast.unparse(node.func)
            for pattern, kind in self._CONCURRENCY_CALLS.items():
                if pattern in func_str:
                    self.concurrency_markers.append(ConcurrencyMarker(
                        file_path=self.file_path,
                        line=node.lineno,
                        kind=kind,
                        detail=func_str,
                    ))
                    break
        except Exception:
            pass
        self.generic_visit(node)


def _parse_python(file_path: str) -> FileSymbols:
    """Parse a Python source file and extract its symbols."""
    module_name = Path(file_path).stem
    result = FileSymbols(file_path=file_path, language="python", package=module_name)
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        result.parse_error = f"SyntaxError: {exc}"
        log.warning("Python parse error in %s: %s", file_path, exc)
        return result
    except OSError as exc:
        result.parse_error = f"OSError: {exc}"
        log.warning("Cannot read %s: %s", file_path, exc)
        return result

    visitor = _PythonVisitor(file_path, module_name)
    visitor.visit(tree)
    result.functions = visitor.functions
    result.types = visitor.types
    result.imports = list(dict.fromkeys(visitor.imports))  # deduplicate
    result.concurrency_markers = visitor.concurrency_markers
    return result


# ---------------------------------------------------------------------------
# Go source parser (regex-based; tree-sitter optional enhancement)
# ---------------------------------------------------------------------------

# Patterns — intentionally conservative to avoid false positives.
_GO_PKG_RE = re.compile(r"^\s*package\s+(\w+)", re.MULTILINE)
_GO_IMPORT_RE = re.compile(r'"([^"]+)"')
_GO_FUNC_RE = re.compile(
    r"^func\s+(?:\((?P<recv>[^)]+)\)\s+)?(?P<name>[A-Za-z_]\w*)"
    r"\s*\((?P<params>[^)]*)\)(?:\s*\((?P<multi_ret>[^)]*)\)|\s*(?P<ret>[^{]*))?",
    re.MULTILINE,
)
_GO_TYPE_RE = re.compile(
    r"^type\s+(?P<name>[A-Za-z_]\w+)\s+(?P<kind>struct|interface)\s*\{",
    re.MULTILINE,
)
_GO_CHAN_SEND_RE = re.compile(r"\w+\s*<-")
_GO_CHAN_RECV_RE = re.compile(r"<-\s*\w+")
_GO_GOROUTINE_RE = re.compile(r"\bgo\s+\w+")
_GO_MUTEX_LOCK_RE = re.compile(r"\.\s*Lock\s*\(")
_GO_MUTEX_UNLOCK_RE = re.compile(r"\.\s*Unlock\s*\(")
_GO_CTX_CANCEL_RE = re.compile(r"\bWithCancel\b|\bcancel\s*\(\)")
_GO_CTX_DEADLINE_RE = re.compile(r"\bWithDeadline\b")
_GO_CTX_TIMEOUT_RE = re.compile(r"\bWithTimeout\b")
_GO_WAITGROUP_RE = re.compile(r"sync\.WaitGroup")
_GO_FUNC_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(|\b([A-Za-z_]\w+)\s*\(")


def _parse_go(file_path: str) -> FileSymbols:
    """
    Parse a Go source file using regex-based heuristics.

    Falls back gracefully for files that use generics or complex syntax
    that the regexes cannot handle, recording a parse_error note without
    crashing.
    """
    result = FileSymbols(file_path=file_path, language="go")
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.parse_error = f"OSError: {exc}"
        log.warning("Cannot read %s: %s", file_path, exc)
        return result

    # Package
    pkg_match = _GO_PKG_RE.search(source)
    result.package = pkg_match.group(1) if pkg_match else Path(file_path).parent.name

    # Imports
    import_block = re.search(r"import\s*\(([^)]*)\)", source, re.DOTALL)
    if import_block:
        result.imports = _GO_IMPORT_RE.findall(import_block.group(1))
    else:
        single = re.findall(r'import\s+"([^"]+)"', source)
        result.imports = single

    # Functions
    for m in _GO_FUNC_RE.finditer(source):
        name = m.group("name")
        recv_raw = m.group("recv") or ""
        receiver: Optional[str] = None
        if recv_raw:
            # Extract type name from "r *ReceiverType" or "r ReceiverType"
            recv_parts = recv_raw.strip().split()
            if recv_parts:
                receiver = recv_parts[-1].lstrip("*")

        params_raw = m.group("params") or ""
        params = [p.strip() for p in params_raw.split(",") if p.strip()]

        ret_raw = (m.group("multi_ret") or m.group("ret") or "").strip()
        returns = [r.strip() for r in ret_raw.split(",") if r.strip()]

        pkg = result.package
        qualname = f"{pkg}.{receiver}.{name}" if receiver else f"{pkg}.{name}"

        lineno = source[: m.start()].count("\n") + 1
        sig = FunctionSignature(
            name=name,
            qualname=qualname,
            file_path=file_path,
            line=lineno,
            is_exported=_is_exported_go(name),
            params=params,
            returns=returns,
            is_method=bool(receiver),
            receiver=receiver,
        )
        result.functions.append(sig)

    # Types
    for m in _GO_TYPE_RE.finditer(source):
        name = m.group("name")
        kind = m.group("kind")
        lineno = source[: m.start()].count("\n") + 1
        result.types.append(TypeDeclaration(
            name=name,
            file_path=file_path,
            line=lineno,
            is_exported=_is_exported_go(name),
            kind=kind,
        ))

    # Concurrency markers — line-by-line scan
    for lineno, line in enumerate(source.splitlines(), start=1):
        def _mark(kind: str, detail: str = "") -> None:
            result.concurrency_markers.append(
                ConcurrencyMarker(file_path=file_path, line=lineno, kind=kind, detail=detail)
            )

        if _GO_GOROUTINE_RE.search(line):
            _mark("goroutine", line.strip())
        if _GO_CHAN_SEND_RE.search(line):
            _mark("channel_send", line.strip())
        if _GO_CHAN_RECV_RE.search(line):
            _mark("channel_recv", line.strip())
        if _GO_MUTEX_LOCK_RE.search(line):
            _mark("mutex_lock", line.strip())
        if _GO_MUTEX_UNLOCK_RE.search(line):
            _mark("mutex_unlock", line.strip())
        if _GO_CTX_CANCEL_RE.search(line):
            _mark("context_cancel", line.strip())
        if _GO_CTX_DEADLINE_RE.search(line):
            _mark("context_deadline", line.strip())
        if _GO_CTX_TIMEOUT_RE.search(line):
            _mark("context_timeout", line.strip())
        if _GO_WAITGROUP_RE.search(line):
            _mark("waitgroup", line.strip())

    return result


# ---------------------------------------------------------------------------
# Public API: parse_ast_symbols
# ---------------------------------------------------------------------------


def parse_ast_symbols(file_path: str) -> FileSymbols:
    """
    Parse a single source file and return its extracted symbol table.

    Supports Python (.py) and Go (.go) files. For unsupported extensions
    an empty ``FileSymbols`` is returned with a ``parse_error`` note.

    Parameters
    ----------
    file_path:
        Absolute or workspace-relative path to the source file.

    Returns
    -------
    FileSymbols
        All functions, types, imports, and concurrency markers found.
        ``parse_error`` is non-None if the file could not be fully parsed.
    """
    lang = _detect_language(file_path)
    if lang == "python":
        return _parse_python(file_path)
    if lang == "go":
        return _parse_go(file_path)

    log.debug("Unsupported file type, skipping: %s", file_path)
    return FileSymbols(
        file_path=file_path,
        language="unknown",
        parse_error=f"Unsupported extension: {Path(file_path).suffix}",
    )


# ---------------------------------------------------------------------------
# Public API: build_call_graph
# ---------------------------------------------------------------------------

def _extract_go_call_edges(fs: FileSymbols, source: str) -> List[CallEdge]:
    """Heuristically extract call edges from Go source."""
    edges: List[CallEdge] = []
    # Build a map of func name → qualname for this file's own symbols.
    local_funcs: Dict[str, str] = {f.name: f.qualname for f in fs.functions}

    # Walk each function body (approximate: just scan the whole file per func)
    for func in fs.functions:
        for lineno, line in enumerate(source.splitlines(), start=1):
            if lineno < func.line:
                continue
            for m in _GO_FUNC_CALL_RE.finditer(line):
                if m.group(1) and m.group(2):
                    # pkg.Func() or receiver.Method()
                    callee = f"{m.group(1)}.{m.group(2)}"
                else:
                    callee_name = m.group(3) or ""
                    if not callee_name or callee_name in {"if", "for", "switch",
                                                          "return", "make", "len",
                                                          "cap", "append", "copy",
                                                          "delete", "new", "panic",
                                                          "recover", "close", "print",
                                                          "println"}:
                        continue
                    callee = local_funcs.get(callee_name, f"{fs.package}.{callee_name}")

                edges.append(CallEdge(
                    caller_qualname=func.qualname,
                    caller_file=fs.file_path,
                    caller_line=lineno,
                    callee_qualname=callee,
                ))
    return edges


def _extract_python_call_edges(fs: FileSymbols, tree: ast.AST) -> List[CallEdge]:
    """Extract call edges from a Python AST."""
    edges: List[CallEdge] = []
    local_funcs: Dict[str, str] = {f.name: f.qualname for f in fs.functions}

    class _CallVisitor(ast.NodeVisitor):
        def __init__(self, caller_sig: FunctionSignature) -> None:
            self.caller = caller_sig

        def visit_Call(self, node: ast.Call) -> None:
            try:
                callee_str = ast.unparse(node.func)
            except Exception:
                self.generic_visit(node)
                return
            # Resolve local calls
            callee_qualname = local_funcs.get(callee_str.split("(")[0], callee_str)
            edges.append(CallEdge(
                caller_qualname=self.caller.qualname,
                caller_file=self.caller.file_path,
                caller_line=node.lineno,
                callee_qualname=callee_qualname,
            ))
            self.generic_visit(node)

    # Map function name → AST node for body traversal
    func_nodes: Dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    class _FuncFinder(ast.NodeVisitor):
        def visit_FunctionDef(self, n: ast.FunctionDef) -> None:
            func_nodes[n.name] = n
            self.generic_visit(n)

        def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> None:
            func_nodes[n.name] = n
            self.generic_visit(n)

    _FuncFinder().visit(tree)

    for sig in fs.functions:
        node = func_nodes.get(sig.name)
        if node:
            _CallVisitor(sig).visit(node)

    return edges


def build_call_graph(files: List[str]) -> CallGraph:
    """
    Build a directed cross-file call graph from the supplied source files.

    Each edge records ``(caller_qualname, caller_file, caller_line,
    callee_qualname)``. Callees that resolve to known symbols in the
    provided file list are annotated with ``callee_file``; unresolved
    external callees leave ``callee_file=None``.

    Parameters
    ----------
    files:
        List of absolute or relative paths to Go or Python source files.

    Returns
    -------
    CallGraph
        Call graph with populated edges and pre-built lookup indexes.
    """
    all_symbols: List[FileSymbols] = []
    py_trees: Dict[str, ast.AST] = {}

    for fp in files:
        fs = parse_ast_symbols(fp)
        all_symbols.append(fs)
        if fs.language == "python" and fs.parse_error is None:
            try:
                source = Path(fp).read_text(encoding="utf-8", errors="replace")
                py_trees[fp] = ast.parse(source, filename=fp)
            except Exception:
                pass

    # Build global qualname → file mapping from all symbols
    qualname_to_file: Dict[str, str] = {}
    for fs in all_symbols:
        for func in fs.functions:
            qualname_to_file[func.qualname] = fs.file_path
        for td in fs.types:
            qualname_to_file[f"{fs.package}.{td.name}"] = fs.file_path

    cg = CallGraph()

    for fs in all_symbols:
        if fs.parse_error:
            continue
        if fs.language == "python":
            tree = py_trees.get(fs.file_path)
            if tree:
                cg.edges.extend(_extract_python_call_edges(fs, tree))
        elif fs.language == "go":
            try:
                source = Path(fs.file_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cg.edges.extend(_extract_go_call_edges(fs, source))

    # Annotate callee_file where resolvable
    for edge in cg.edges:
        if edge.callee_file is None:
            edge.callee_file = qualname_to_file.get(edge.callee_qualname)

    cg.build_indexes()
    return cg


# ---------------------------------------------------------------------------
# Public API: compute_blast_radius
# ---------------------------------------------------------------------------


def compute_blast_radius(
    changed_files: List[str],
    diff_symbols: Set[str],
    *,
    call_graph: Optional[CallGraph] = None,
    all_files: Optional[List[str]] = None,
) -> BlastRadius:
    """
    Compute which files and packages are downstream of changed public symbols.

    The function performs a BFS/DFS over the call graph starting from every
    edge whose ``callee_qualname`` is in ``diff_symbols``, collecting all
    transitively impacted callers.

    Parameters
    ----------
    changed_files:
        Source files that were directly modified (e.g. from ``git diff``).
    diff_symbols:
        Set of qualified symbol names that changed (e.g. exported functions
        or struct fields whose signatures were modified).
    call_graph:
        Pre-built ``CallGraph``. When *None*, one is constructed on-the-fly
        from ``all_files`` (or ``changed_files`` only as a fallback).
    all_files:
        Full list of repository files used to build the call graph when
        ``call_graph`` is not supplied.

    Returns
    -------
    BlastRadius
        Detailed impact report with directly and transitively impacted files,
        affected packages, and the specific broken call edges.
    """
    if call_graph is None:
        sources = all_files or changed_files
        call_graph = build_call_graph(sources)

    # Identify all edges directly calling a changed symbol
    broken_edges: List[CallEdge] = []
    for sym in diff_symbols:
        broken_edges.extend(call_graph.callers_of(sym))

    directly_impacted: Set[str] = {e.caller_file for e in broken_edges}

    # Transitive BFS over caller qualnames
    visited_syms: Set[str] = set(diff_symbols)
    frontier: Set[str] = {e.caller_qualname for e in broken_edges}
    transitive_files: Set[str] = set(directly_impacted)

    while frontier:
        next_frontier: Set[str] = set()
        for sym in frontier:
            if sym in visited_syms:
                continue
            visited_syms.add(sym)
            for edge in call_graph.callers_of(sym):
                transitive_files.add(edge.caller_file)
                next_frontier.add(edge.caller_qualname)
        frontier = next_frontier

    # Derive impacted packages from file paths
    def _pkg_from_file(fp: str) -> str:
        """Best-effort package name from file path."""
        p = Path(fp)
        if p.suffix == ".go":
            return p.parent.name
        return p.stem

    impacted_packages: Set[str] = {_pkg_from_file(f) for f in transitive_files}

    return BlastRadius(
        changed_symbols=set(diff_symbols),
        directly_impacted_files=directly_impacted,
        transitively_impacted_files=transitive_files,
        impacted_packages=impacted_packages,
        broken_edges=broken_edges,
    )


# ---------------------------------------------------------------------------
# Repository indexer
# ---------------------------------------------------------------------------

_SOURCE_EXTENSIONS: FrozenSet[str] = frozenset({".py", ".go"})


def _walk_source_files(root: str) -> List[str]:
    """Recursively collect all supported source files under *root*."""
    results: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden dirs and common vendor/cache directories
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".")
            and d not in {"vendor", "node_modules", "__pycache__", ".git", "dist", "build"}
        ]
        for fn in filenames:
            if Path(fn).suffix.lower() in _SOURCE_EXTENSIONS:
                results.append(os.path.join(dirpath, fn))
    return results


def build_repository_index(root: str) -> RepositoryIndex:
    """
    Index an entire repository rooted at *root*.

    1. Walks the directory tree, collecting all ``.py`` and ``.go`` files.
    2. Calls :func:`parse_ast_symbols` on each file.
    3. Calls :func:`build_call_graph` across all collected files.
    4. Populates ``RepositoryIndex.package_files`` and the symbol definition
       quick-lookup table.

    Parameters
    ----------
    root:
        Absolute path to the repository root.

    Returns
    -------
    RepositoryIndex
        Fully populated index ready for blast-radius queries.
    """
    root = os.path.abspath(root)
    files = _walk_source_files(root)
    log.info("Indexing %d source files under %s", len(files), root)

    index = RepositoryIndex(root=root)

    for fp in files:
        fs = parse_ast_symbols(fp)
        index.file_symbols[fp] = fs
        index.package_files.setdefault(fs.package, []).append(fp)
        for func in fs.functions:
            index._symbol_defs[func.qualname] = fp
        for td in fs.types:
            index._symbol_defs[f"{fs.package}.{td.name}"] = fp

    index.call_graph = build_call_graph(files)
    log.info("Call graph: %d edges", len(index.call_graph.edges))
    return index
