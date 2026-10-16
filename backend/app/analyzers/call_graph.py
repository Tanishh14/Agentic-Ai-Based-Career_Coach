"""Approximate static reference index across the scanned scope.

Used to tell whether a function holding evidence is referenced anywhere (imported,
called, registered). Static name matching cannot prove runtime reachability; results
are labelled `referenced`, `entrypoint`, `unreferenced` or `unknown` and the README
states the limitation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ENTRYPOINT_NODES = {"decorated_route_handler", "class_based_view", "route_registration", "route_handler", "controller_method",
                    "test_function", "test_case", "test_method", "url_registration", "auth_decorator"}
NA_NODES = {"dockerfile", "compose_services", "workflow", "ci_test_step", "deploy_step", "platform_config", "k8s_manifest", "k8s_workload",
            "iac_resources", "cloud_iac", "dependency_declaration", "sql_schema_or_query", "sql_file", "helm_chart", "healthcheck",
            "tailwind_config", "tailwind_css", "stylesheet", "html_document", "shell_script", "graphql_schema", "iac_template",
            "compose_file", "test_file", "module", "source_file", "single_file_component", "schema_migration"}


@dataclass
class ReferenceIndex:
    refs: dict[str, dict[str, int]] = field(default_factory=dict)  # name -> {path: count}
    defs: dict[str, set[str]] = field(default_factory=dict)  # name -> paths defining it
    imports_by_path: dict[str, set[str]] = field(default_factory=dict)

    def add_file(self, path: str, referenced: dict[str, int], defined: set[str]) -> None:
        for name, cnt in referenced.items():
            self.refs.setdefault(name, {})[path] = cnt
        for d in defined:
            self.defs.setdefault(d, set()).add(path)

    def reachability(self, symbol: str | None, path: str, node_type: str) -> str:
        if node_type in ENTRYPOINT_NODES:
            return "entrypoint"
        if node_type in NA_NODES:
            return "not_applicable"
        if not symbol:
            return "module_scope"
        name = symbol.split(".")[-1]
        if name.startswith("__") and name.endswith("__"):
            return "referenced"
        where = self.refs.get(name, {})
        other = sum(c for p, c in where.items() if p != path)
        same = where.get(path, 0)
        # JS identifier counts include the definition itself; Python Name counts do not.
        same_threshold = 1 if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".go", ".kt")) else 0
        if other > 0 or same > same_threshold:
            return "referenced"
        return "unreferenced"

    def callers(self, symbol: str) -> list[tuple[str, int]]:
        name = symbol.split(".")[-1]
        return sorted(self.refs.get(name, {}).items(), key=lambda kv: -kv[1])


def find_references_in_text(text: str, name: str, limit: int = 20) -> list[tuple[int, str]]:
    out = []
    pat = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
    for i, line in enumerate(text.split("\n"), 1):
        if pat.search(line):
            out.append((i, line.strip()[:200]))
            if len(out) >= limit:
                break
    return out
