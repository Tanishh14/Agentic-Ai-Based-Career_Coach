"""Shared finding type produced by every deterministic detector."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DETECTOR_VERSION = "1.2.0"

DEPTH_ORDER = {"trivial": 0, "basic": 1, "intermediate": 2, "substantial": 3}

# Signal levels distinguish what kind of proof a finding is (never blended):
#   dependency_detected       -> package declared in a manifest
#   config_present            -> configuration file exists with real content
#   code_usage                -> library/API actually used in code
#   meaningful_implementation -> non-trivial implementation (handler logic, executed SQL...)
#   tests_found               -> test with meaningful assertions
SIGNAL_ORDER = {"dependency_detected": 0, "config_present": 1, "code_usage": 2, "tests_found": 3, "meaningful_implementation": 3}


@dataclass
class Finding:
    skill_key: str
    detector: str
    start_line: int
    end_line: int
    node_type: str
    signal_level: str
    depth: str
    source_type: str  # CODE_IMPLEMENTATION | CODE_TEST | CODE_CONFIG | DEPENDENCY_DECLARATION
    reason: str
    candidate_status: str = "accepted"  # accepted | rejected | ambiguous (detector's own view)
    symbol: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def clamp(self, n_lines: int) -> "Finding":
        n_lines = max(1, n_lines)
        self.start_line = max(1, min(self.start_line, n_lines))
        self.end_line = max(self.start_line, min(self.end_line, n_lines))
        return self


def depth_from_size(n: int, basic: int = 1, intermediate: int = 3, substantial: int = 8) -> str:
    if n >= substantial:
        return "substantial"
    if n >= intermediate:
        return "intermediate"
    if n >= basic:
        return "basic"
    return "trivial"
