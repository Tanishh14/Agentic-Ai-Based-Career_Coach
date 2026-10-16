# Evidence Engine & Deterministic Detector Pipeline

This document details the deterministic static code analysis engine, AST detectors, comment stripping rules, and adversarial critic verifier implemented in `backend/app/analyzers` and `backend/app/agents`.

---

## 🎯 Design Principles

1. **Deterministic First**: Primary evidence is derived from concrete syntax trees and AST representations. Heuristics and regex are used only where language-native ASTs are unavailable.
2. **Comment & Docstring Stripping**: Any mention of keywords (e.g., SQL queries, FastAPI routes, test asserts) inside comments, documentation, or string literals is strictly excluded from technical qualification.
3. **No Credit for Declaration Without Use**: Declaring a dependency in `pyproject.toml` or `package.json` earns at most `PARTIAL` ("declaration only"). Active imports, calls, or subclassing are required for `DEMONSTRATED`.

---

## 🔍 Language Analyzers

### 1. Python AST Analyzer (`pyast.py`, `detect_python.py`)
- Traverses Python AST using `ast.NodeVisitor`.
- Identifies FastAPI and Flask endpoints by inspecting decorator names: `@app.get`, `@app.post`, `@router.get`, `@bp.route`.
- Tracks database sessions: `session.query()`, `select()`, `execute()`, model definitions subclassing `Base` or `DeclarativeBase`.
- Evaluates test assertions: `assert <expr>` inside functions prefixed with `test_` or classes inheriting from `unittest.TestCase`. Trivial assertions (`assert True`, `assert 1 == 1`) are flagged as invalid.

### 2. Lexical C-Like Detectors (`detect_clike.py`, `lexer.py`)
- Applied to TypeScript, JavaScript, Go, Rust, Java, C#, and PHP.
- Strips single-line (`//`, `#`) and multi-line (`/* ... */`) comments.
- Tracks balanced curly brace depth to attribute statements to appropriate function scopes.
- Detects Express/Nest route registrations, database drivers, and unit test suites (Jest, Vitest, JUnit).

### 3. Config & Manifest Detectors (`detect_config.py`, `libraries.py`)
- Analyzes `Dockerfile` (`FROM`, `RUN`, `EXPOSE`), `docker-compose.yml` service architectures, and GitHub Actions workflow triggers (`on: push`, `jobs.build.steps`).
- Distinguishes active execution configs from boilerplate templates.

---

## 🛡️ Adversarial Critic Pass (`evidence_verifier.py`)

Every snippet identified by deterministic detectors undergoes an adversarial critic pass:
- **Triviality Filter**: Rejects stub implementations (`pass`, `...`, `return None`, `TODO`).
- **Depth Scoring**:
  - `trivial`: Empty stub or trivial assertion.
  - `surface`: Minimum valid syntax without realistic error handling or parameterization.
  - `working`: Functional implementation with active parameters and execution.
  - `production`: Robust design with error handling, type annotations, and automated test coverage.
