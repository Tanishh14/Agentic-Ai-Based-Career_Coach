"""Detector dispatcher: routes a file to the right language/config detectors.

Returns findings plus a per-file symbol summary used for reachability analysis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .detect_clike import scan_go, scan_java, scan_js, scan_other
from .detect_config import scan_config
from .detect_python import scan_python
from .files import file_kind, language_of
from .findings import DETECTOR_VERSION, Finding

DETECTOR_FAMILIES = [
    "api_backend (Python AST routes/CBVs, Express/Fastify/Next.js/NestJS, Spring, Go, ASP.NET, Rails, Laravel)",
    "sql_database (executed SQL, ORM queries/models, migrations, .sql files; SQL text without execution = ambiguous)",
    "automated_testing (test functions with meaningful assertions; empty/assert True rejected; CI test steps as supporting signal)",
    "ci_cd (GitHub Actions/GitLab/CircleCI/Jenkins with meaningful commands; echo-only rejected)",
    "containerization (Dockerfile, Compose)",
    "deployment (platform configs, deploy steps, k8s workloads, healthchecks)",
    "auth_security, caching, concurrency, observability, oop",
    "library usage (import + usage, not just import) for React, Next.js, Vue, Angular, Express, NestJS, Django, Flask, FastAPI, Spring, GraphQL, pandas, ML, LLM SDKs, NoSQL, message queues, Tailwind",
    "cloud (AWS/GCP/Azure SDK usage or IaC), kubernetes, infrastructure-as-code",
    "language usage (non-trivial source files per language)",
    "dependency manifests (declaration only — never sufficient on its own)",
]


@dataclass
class FileScan:
    path: str
    language: str | None
    kind: str
    findings: list[Finding] = field(default_factory=list)
    defined_symbols: set[str] = field(default_factory=set)
    referenced_names: dict[str, int] = field(default_factory=dict)
    parse_error: str | None = None


_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def scan_file(path: str, text: str) -> FileScan:
    lang = language_of(path)
    kind = file_kind(path, text[:600])
    fs = FileScan(path=path, language=lang, kind=kind)
    if kind in ("vendor", "docs"):
        return fs
    if kind == "generated":
        # Generated code is never evidence; record it so the verifier can explain.
        return fs
    try:
        if lang == "python":
            findings, mod = scan_python(path, text, kind)
            fs.findings = findings
            if not mod.ok:
                fs.parse_error = mod.error
            fs.defined_symbols = {f.name for f in mod.funcs} | {c.name for c in mod.classes}
            fs.referenced_names = dict(mod.names_used)
        elif lang in ("javascript", "typescript"):
            fs.findings = scan_js(path, text, kind, lang)
            fs.defined_symbols = {a or b for a, b in re.findall(r"\bfunction\s+([\w$]+)|\bclass\s+([\w$]+)", text)}
            fs.referenced_names = _ident_counts(text)
        elif lang in ("java", "kotlin"):
            fs.findings = scan_java(path, text, kind, lang)
            fs.referenced_names = _ident_counts(text)
        elif lang == "go":
            fs.findings = scan_go(path, text, kind)
            fs.referenced_names = _ident_counts(text)
        elif lang in ("csharp", "rust", "php", "ruby", "swift", "scala", "cpp", "c"):
            fs.findings = scan_other(path, text, kind, lang)
        if lang in ("vue", "svelte"):
            if ("<script" in text and "<template" in text) or lang == "svelte":
                fs.findings.append(Finding("vue" if lang == "vue" else "javascript", f"{lang}_component", 1, min(40, text.count("\n") + 1),
                                     "single_file_component", "code_usage", "basic" if text.count("\n") < 80 else "intermediate",
                                     "CODE_IMPLEMENTATION", f"{lang.title()} single-file component."))
        fs.findings += scan_config(path, text, lang)
    except RecursionError:
        fs.parse_error = "file too deeply nested to analyse"
    for f in fs.findings:
        f.meta.setdefault("detector_version", DETECTOR_VERSION)
    return fs


def _ident_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in _IDENT.finditer(text):
        counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    return counts


def detectors_for_path(path: str) -> list[str]:
    """Which skill families can be affected by a change to this path (for incremental reassessment)."""
    lang = language_of(path)
    kind = file_kind(path)
    low = path.lower()
    if kind == "manifest":
        return ["config"]
    if kind == "docs":
        return []
    if lang in ("markdown", "rst", "text"):
        return []
    fams: list[str] = []
    if lang in ("python", "javascript", "typescript", "java", "kotlin", "go", "csharp", "rust", "php", "ruby", "swift", "scala", "cpp", "c", "vue", "svelte"):
        fams += ["code"]
    if lang == "sql":
        fams.append("sql_file")
    if lang in ("html_css",):
        fams.append("html_css")
    if lang in ("yaml", "json", "toml", "terraform", "dockerfile", "groovy", "xml", "gradle", "ini", "shell") or "procfile" in low:
        fams.append("config")
    return fams
