"""
rules.py — Structured invariant rule specifications for StampBob's Invariant Oracle.

Each rule is a dataclass that describes:
  - A unique rule ID and human-readable description
  - The severity StampBob should report when the rule fires
  - Language applicability (Go, Python, or both)
  - A set of compiled regex/AST patterns used by the oracle to detect violations
  - Precise remediation guidance surfaced in violation reports

Rules are *pure data* — no analysis logic lives here.  The oracle in
``invariant_oracle.py`` imports and consumes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, List, Pattern


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"


class Language(str, Enum):
    GO = "go"
    PYTHON = "python"
    ANY = "any"


# ---------------------------------------------------------------------------
# Base rule dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantRule:
    """
    Immutable specification for a single repository invariant.

    Attributes
    ----------
    rule_id:
        Short, stable kebab-case identifier (e.g. ``goroutine-leak``).
    description:
        One-line human-readable summary.
    severity:
        ``CRITICAL`` for correctness/safety violations; ``WARNING`` for
        best-practice deviations.
    languages:
        Set of languages this rule applies to.
    remediation:
        Actionable fix guidance included in violation reports.
    """

    rule_id: str
    description: str
    severity: Severity
    languages: FrozenSet[Language]
    remediation: str


# ---------------------------------------------------------------------------
# Rule 1 — Goroutine Leak
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GoroutineLeakRule(InvariantRule):
    """
    Flags any ``go func()`` anonymous goroutine spawn that is not linked to
    a ``context.Context`` cancellation signal or a guaranteed exit mechanism
    (``WaitGroup``, channel close, or ``defer``).

    Pattern logic (Go only):
      TRIGGER  — line matches ``go func(`` (anonymous goroutine literal)
      SAFE     — within the same enclosing function body there is at least
                 one of: ``ctx``, ``cancel``, ``sync.WaitGroup``,
                 ``<-done``, ``<-quit``, ``<-stop``, or ``defer``
    """

    # Detects: go func(   (anonymous goroutine)
    spawn_pattern: Pattern[str] = field(
        default=re.compile(r"\bgo\s+func\s*\("),
        compare=False,
    )
    # Safety signals that must appear somewhere in the enclosing function body
    safety_patterns: List[Pattern[str]] = field(
        default_factory=lambda: [
            re.compile(r"\bctx\b"),
            re.compile(r"\bcancel\s*\("),
            re.compile(r"sync\.WaitGroup"),
            re.compile(r"<-\s*(?:done|quit|stop|ctx\.Done\(\))"),
            re.compile(r"\bdefer\s+cancel\b"),
        ],
        compare=False,
    )


GOROUTINE_LEAK_RULE = GoroutineLeakRule(
    rule_id="goroutine-leak",
    description=(
        "Anonymous goroutine spawned without a context cancellation "
        "or guaranteed exit signal — potential goroutine leak."
    ),
    severity=Severity.CRITICAL,
    languages=frozenset({Language.GO}),
    remediation=(
        "Pass a context.Context to the goroutine and select on ctx.Done(), "
        "or use a sync.WaitGroup with a paired Done() call to ensure the "
        "goroutine terminates."
    ),
)


# ---------------------------------------------------------------------------
# Rule 2 — Unbuffered Channel in Hot Loop
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnbufferedChannelRule(InvariantRule):
    """
    Flags unbuffered ``make(chan …)`` allocations that appear inside hot
    execution loops (``for``, ``range``) or worker-pool dispatch functions.

    An unbuffered channel inside a tight loop can cause worker-pool deadlocks
    when producers and consumers are not perfectly balanced.

    Pattern logic (Go only):
      TRIGGER  — ``make(chan`` without a capacity argument (no comma)
                 AND the allocation sits inside a ``for`` or ``range`` block
      SAFE     — capacity argument present, i.e. ``make(chan T, N)``
    """

    # Detects: make(chan SomeType)  — no buffer size
    unbuffered_pattern: Pattern[str] = field(
        default=re.compile(r"\bmake\s*\(\s*chan\s+[^,)]+\)"),
        compare=False,
    )
    # Loop indicators that must appear in the enclosing context
    loop_patterns: List[Pattern[str]] = field(
        default_factory=lambda: [
            re.compile(r"\bfor\b"),
            re.compile(r"\brange\b"),
        ],
        compare=False,
    )


UNBUFFERED_CHANNEL_RULE = UnbufferedChannelRule(
    rule_id="unbuffered-channel-in-loop",
    description=(
        "Unbuffered channel allocated inside a loop or worker-dispatch "
        "context — may cause worker-pool deadlock."
    ),
    severity=Severity.CRITICAL,
    languages=frozenset({Language.GO}),
    remediation=(
        "Provide an explicit buffer capacity: make(chan T, N). "
        "Size the buffer to the maximum number of concurrent producers "
        "to prevent blocking sends."
    ),
)


# ---------------------------------------------------------------------------
# Rule 3 — Nil Check Boundary
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NilCheckBoundaryRule(InvariantRule):
    """
    Flags pointer/interface dereferences that lack an antecedent
    ``if ptr == nil`` (Go) or ``is None`` (Python) guard within the
    immediately enclosing scope.

    Pattern logic (Go + Python):
      Go TRIGGER   — ``ptr.Field`` or ``ptr.Method()`` where ptr is a
                     known pointer/interface and no ``if ptr == nil`` or
                     ``if ptr != nil`` precedes the dereference in the
                     function body.
      Python TRIGGER — attribute access ``obj.attr`` after the object
                       was assigned from a function that may return None
                       (heuristic: return type annotation contains
                       ``Optional`` or ``| None``).
    """

    # Go: any "identifier.something" dereference
    go_deref_pattern: Pattern[str] = field(
        default=re.compile(r"\b([a-z_]\w*)\.\w+"),
        compare=False,
    )
    # Go: preceding nil guard in the same function body
    go_nil_guard_pattern: Pattern[str] = field(
        default=re.compile(r"\bif\s+\w+\s*[!=]=\s*nil\b"),
        compare=False,
    )
    # Python: attribute access on a name
    py_deref_pattern: Pattern[str] = field(
        default=re.compile(r"\b([a-z_]\w*)\.(?!__)\w+"),
        compare=False,
    )
    # Python: None guard
    py_none_guard_pattern: Pattern[str] = field(
        default=re.compile(r"\bif\s+\w+\s+is\s+(?:not\s+)?None\b"),
        compare=False,
    )


NIL_CHECK_BOUNDARY_RULE = NilCheckBoundaryRule(
    rule_id="nil-check-boundary",
    description=(
        "Pointer/interface dereference without a preceding nil/None guard "
        "in the enclosing scope — potential nil dereference panic or AttributeError."
    ),
    severity=Severity.CRITICAL,
    languages=frozenset({Language.GO, Language.PYTHON}),
    remediation=(
        "Add an explicit nil check before dereferencing: "
        "``if ptr == nil { return err }`` (Go) or "
        "``if obj is None: raise ValueError(...)`` (Python)."
    ),
)


# ---------------------------------------------------------------------------
# Rule 4 — OpenTelemetry Semantic Convention
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenTelemetrySemanticRule(InvariantRule):
    """
    Verifies that span attribute keys follow CNCF OpenTelemetry semantic
    conventions (``gen_ai.*``, ``server.*``, ``http.*``, ``db.*``, etc.)
    and rejects ad-hoc, non-standard attribute names.

    Approved key prefixes (CNCF semconv 1.24+):
        gen_ai, server, client, http, rpc, db, messaging, faas,
        cloud, container, host, k8s, network, process, code, enduser,
        exception, log, thread, url

    Pattern logic (Go + Python):
      TRIGGER  — ``span.SetAttribute(`` or ``tracer.Start(`` or
                 ``otel.SetAttributes(`` where the key string does *not*
                 start with an approved prefix.
    """

    # Approved CNCF semconv prefixes
    approved_prefixes: FrozenSet[str] = field(
        default=frozenset({
            "gen_ai", "server", "client", "http", "rpc", "db",
            "messaging", "faas", "cloud", "container", "host",
            "k8s", "network", "process", "code", "enduser",
            "exception", "log", "thread", "url", "peer", "error",
        }),
        compare=False,
    )
    # Detects span attribute setting — both Go and Python OTel APIs
    set_attr_pattern: Pattern[str] = field(
        default=re.compile(
            r'(?:attribute\.String|attribute\.Int|attribute\.Bool'
            r'|semconv\.\w+'
            r'|span\.SetAttributes?'
            r'|SetAttributes?)'
            r'\s*\(\s*"([^"]+)"'
        ),
        compare=False,
    )
    # Python opentelemetry-sdk style: trace.set_attribute("key", value)
    py_set_attr_pattern: Pattern[str] = field(
        default=re.compile(r'\.set_attribute\s*\(\s*["\']([^"\']+)["\']'),
        compare=False,
    )


OTEL_SEMANTIC_RULE = OpenTelemetrySemanticRule(
    rule_id="otel-semantic-convention",
    description=(
        "Span attribute key does not conform to CNCF OpenTelemetry semantic "
        "conventions — ad-hoc attribute names break observability tooling."
    ),
    severity=Severity.WARNING,
    languages=frozenset({Language.GO, Language.PYTHON}),
    remediation=(
        "Use an approved CNCF semconv prefix such as gen_ai.*, server.*, "
        "http.*, db.*, or rpc.*. "
        "See https://opentelemetry.io/docs/specs/semconv/ for the full registry."
    ),
)


# ---------------------------------------------------------------------------
# Registry — all rules in priority order
# ---------------------------------------------------------------------------

ALL_RULES: List[InvariantRule] = [
    GOROUTINE_LEAK_RULE,
    UNBUFFERED_CHANNEL_RULE,
    NIL_CHECK_BOUNDARY_RULE,
    OTEL_SEMANTIC_RULE,
]
