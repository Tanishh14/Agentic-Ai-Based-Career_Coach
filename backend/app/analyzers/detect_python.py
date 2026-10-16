"""Python detectors (AST-based)."""
from __future__ import annotations

import ast
import re

from .findings import Finding, depth_from_size
from .libraries import LIBS
from .pyast import SQL_RE, PyFunc, PyModule, dotted, enclosing_func, func_by_qualname, parse_python

ROUTE_ATTRS = {"get", "post", "put", "delete", "patch", "route", "api_route", "websocket", "head", "options"}
CBV_BASES = {"APIView", "GenericAPIView", "ViewSet", "ModelViewSet", "GenericViewSet", "View", "MethodView", "Resource",
             "ListAPIView", "RetrieveAPIView", "CreateAPIView", "ListCreateAPIView", "RetrieveUpdateDestroyAPIView", "HTTPEndpoint"}
EXEC_ATTRS = {"execute", "executemany", "executescript", "exec_driver_sql", "raw", "fetch", "fetchrow", "fetchval", "mogrify",
              "read_sql", "read_sql_query", "execute_async"}
DJANGO_QS = {"filter", "get", "create", "all", "exclude", "update", "delete", "aggregate", "annotate", "get_or_create",
             "update_or_create", "bulk_create", "values", "order_by", "select_related", "prefetch_related"}
ORM_BASES = {"Base", "DeclarativeBase", "Model", "db.Model", "models.Model", "SQLModel", "Document"}
FIELD_CALLS = re.compile(r"(^|\.)(Column|mapped_column|relationship|ForeignKey|Field|\w+Field|ManyToManyField|ForeignKey)$")
DRIVER_CALLS = {"sqlite3.connect", "psycopg2.connect", "psycopg.connect", "asyncpg.connect", "asyncpg.create_pool",
                "pymysql.connect", "mysql.connector.connect", "create_engine", "create_async_engine", "aiosqlite.connect"}
NOT_SUT_CALLS = {"print", "len", "range", "isinstance", "str", "int", "dict", "list", "set", "tuple", "open", "type", "repr"}


def _snippet_range(f: PyFunc | None, line: int, end: int, cap: int = 45) -> tuple[int, int]:
    if f is not None:
        return f.line, min(f.end_line, f.line + cap)
    return line, max(line, min(end, line + cap))


def _handler_profile(f: PyFunc) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = f.n_statements
    has_try = any(isinstance(x, ast.Try) for x in ast.walk(f.node))
    raises_http = any(r.split(".")[-1] in ("HTTPException", "abort", "ValidationError", "Http404", "NotFound", "BadRequest") for r in f.raises)
    validated = any(a and a[0].isupper() and a not in ("Request", "Response") for a in f.params_annotated)
    if has_try or raises_http:
        notes.append("error handling")
    if validated:
        notes.append("typed/validated request model")
    if f.trivial:
        return "trivial", ["placeholder body (pass / ... / constant return)"]
    score = n + (2 if notes else 0)
    if score >= 7:
        depth = "substantial"
    elif score >= 3:
        depth = "intermediate"
    else:
        depth = "basic"
    return depth, notes


def detect_api(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    for f in mod.funcs:
        for dname, first, dline in f.decorators:
            parts = dname.split(".")
            is_route = (len(parts) >= 2 and parts[-1] in ROUTE_ATTRS and first is not None and (first == "" or first.startswith("/")))
            is_route = is_route or parts[-1] in ("api_view",)
            if not is_route:
                continue
            depth, notes = _handler_profile(f)
            method = parts[-1].upper() if parts[-1] not in ("route", "api_route", "api_view") else "ROUTE"
            out.append(Finding(
                skill_key="api_development", detector="py_route_handler", start_line=dline, end_line=min(f.end_line, f.line + 45),
                node_type="decorated_route_handler", signal_level="meaningful_implementation" if depth in ("intermediate", "substantial") else "code_usage",
                depth=depth, source_type="CODE_IMPLEMENTATION",
                reason=f"{method} {first or ''} handled by `{f.qualname}` ({f.n_statements} statements{', ' + ', '.join(notes) if notes else ''}).",
                candidate_status="rejected" if depth == "trivial" else "accepted", symbol=f.qualname,
                meta={"route": first, "method": method, "decorator": dname},
            ))
            break
    for c in mod.classes:
        if any(b.split(".")[-1] in CBV_BASES for b in c.bases):
            handlers = [m for m in c.methods if m in ("get", "post", "put", "patch", "delete", "list", "create", "retrieve", "update", "destroy")]
            if handlers or any(b.split(".")[-1].endswith("ViewSet") for b in c.bases):
                fs = [f for f in mod.funcs if f.class_name == c.name and f.name in handlers]
                nontrivial = [f for f in fs if not f.trivial]
                depth = depth_from_size(sum(f.n_statements for f in nontrivial), 1, 4, 10) if fs else "basic"
                out.append(Finding(
                    skill_key="api_development", detector="py_class_based_view", start_line=c.line, end_line=min(c.end_line, c.line + 45),
                    node_type="class_based_view", signal_level="meaningful_implementation" if depth in ("intermediate", "substantial") else "code_usage",
                    depth=depth, source_type="CODE_IMPLEMENTATION",
                    reason=f"Class-based view `{c.name}` ({', '.join(c.bases)}) implementing {', '.join(handlers) or 'viewset actions'}.",
                    candidate_status="accepted" if depth != "trivial" else "rejected", symbol=c.name,
                ))
    for call in mod.calls:
        if call.attr in ("path", "re_path", "url") and call.first_str is not None and path.endswith("urls.py") and call.n_args >= 2:
            out.append(Finding(
                skill_key="api_development", detector="django_urlconf", start_line=call.line, end_line=call.end_line,
                node_type="url_registration", signal_level="code_usage", depth="basic", source_type="CODE_IMPLEMENTATION",
                reason=f"Django URL route '{call.first_str}' registered to a view.", symbol=None,
            ))
        elif call.attr == "add_url_rule" and call.first_str and call.first_str.startswith("/"):
            out.append(Finding(
                skill_key="api_development", detector="flask_add_url_rule", start_line=call.line, end_line=call.end_line,
                node_type="url_registration", signal_level="code_usage", depth="basic", source_type="CODE_IMPLEMENTATION",
                reason=f"Flask route '{call.first_str}' registered with add_url_rule.",
            ))
    return out[:12]


def detect_sql(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    executed_lines: set[int] = set()
    by_func: dict[str | None, list[str]] = {}
    for call in mod.calls:
        root = call.name.split(".")[0] if call.name else ""
        sql_text = call.first_str if call.first_str and SQL_RE.search(call.first_str) else None
        via_var = next((a for a in call.arg_names if (call.func, a) in mod.sql_vars or (None, a) in mod.sql_vars), None)
        core_stmt = call.attr in ("execute", "scalars", "scalar") and call.node.args and isinstance(call.node.args[0], ast.Call) and \
            dotted(call.node.args[0].func).split(".")[-1] in ("select", "insert", "update", "delete", "text")
        if call.attr in EXEC_ATTRS and (sql_text or via_var or core_stmt):
            f = func_by_qualname(mod, call.func) or enclosing_func(mod, call.line)
            for ln in range(call.line, call.end_line + 1):
                executed_lines.add(ln)
            if via_var:
                executed_lines.add(mod.sql_vars.get((call.func, via_var)) or mod.sql_vars.get((None, via_var)) or 0)
            stmt = (sql_text or "").strip().split()[0].upper() if sql_text else ("statement variable " + via_var if via_var else "SQLAlchemy Core statement")
            parameterized = call.n_args >= 2 or bool(sql_text and re.search(r"\?|%s|%\(\w+\)s|:\w+|\$\d", sql_text))
            interpolated = isinstance(call.node.args[0], ast.JoinedStr) if call.node.args else False
            by_func.setdefault(f.qualname if f else None, []).append(stmt)
            if (f.qualname if f else None) in [x.symbol for x in out if x.detector == "py_sql_execution"]:
                continue
            fcalls = [c.attr for c in mod.calls if c.func == (f.qualname if f else None)]
            txn = any(a in ("commit", "rollback", "begin") for a in fcalls)
            depth = "intermediate"
            notes = []
            if parameterized:
                notes.append("parameterized")
            if txn:
                notes.append("explicit transaction handling")
                depth = "substantial"
            if interpolated:
                notes.append("string-interpolated SQL (injection risk)")
                depth = "basic"
            s, e = _snippet_range(f, call.line, call.end_line)
            out.append(Finding(
                skill_key="sql", detector="py_sql_execution", start_line=s, end_line=e, node_type="sql_execute_call",
                signal_level="meaningful_implementation", depth=depth, source_type="CODE_IMPLEMENTATION",
                reason=f"Executes SQL ({stmt}) via `{call.name}` in `{f.qualname if f else 'module scope'}`" + (f" — {', '.join(notes)}" if notes else "") + ".",
                symbol=f.qualname if f else None, meta={"statement": stmt, "parameterized": parameterized},
            ))
    # ORM queries
    seen_funcs: set[str | None] = {x.symbol for x in out}
    for call in mod.calls:
        name = call.name
        is_orm = (
            (call.attr == "query" and (name.startswith("session.") or name.startswith("db.session") or name.startswith("self.session") or name.startswith("db.")))
            or (call.attr in DJANGO_QS and ".objects." in name)
            or (call.attr in ("add", "add_all", "merge") and ("session" in name.lower()) and any(c.attr == "commit" and c.func == call.func for c in mod.calls))
        )
        if not is_orm:
            continue
        f = func_by_qualname(mod, call.func) or enclosing_func(mod, call.line)
        key = f.qualname if f else None
        if key in seen_funcs:
            continue
        seen_funcs.add(key)
        s, e = _snippet_range(f, call.line, call.end_line)
        out.append(Finding(
            skill_key="sql", detector="py_orm_query", start_line=s, end_line=e, node_type="orm_query",
            signal_level="meaningful_implementation", depth="intermediate", source_type="CODE_IMPLEMENTATION",
            reason=f"ORM persistence operation `{name}` in `{key or 'module scope'}`.", symbol=key,
        ))
    # ORM models
    for c in mod.classes:
        base_hit = any(b in ORM_BASES or b.endswith(".Model") for b in c.bases)
        sqlmodel_table = "SQLModel" in c.bases and c.keywords.get("table") == "True"
        fields = [fc for fc in c.field_calls if FIELD_CALLS.search(fc[0])]
        if (base_hit and fields and "SQLModel" not in c.bases) or (sqlmodel_table and fields):
            if c.bases == ["Model"] and not any("models" in v or "sqlalchemy" in v or "flask_sqlalchemy" in v for v in mod.imports.values()):
                continue
            out.append(Finding(
                skill_key="sql", detector="py_orm_model", start_line=c.line, end_line=min(c.end_line, c.line + 40),
                node_type="orm_model_class", signal_level="code_usage", depth=depth_from_size(len(fields), 1, 3, 8),
                source_type="CODE_IMPLEMENTATION", reason=f"ORM model `{c.name}` with {len(fields)} mapped column/field definitions.",
                symbol=c.name,
            ))
    # Drivers / migrations
    for call in mod.calls:
        if call.name in DRIVER_CALLS or call.name.endswith(".create_engine"):
            out.append(Finding(
                skill_key="sql", detector="py_db_driver", start_line=call.line, end_line=call.end_line, node_type="db_connection",
                signal_level="code_usage", depth="basic", source_type="CODE_IMPLEMENTATION",
                reason=f"Database connection created with `{call.name}`.", symbol=call.func,
            ))
            break
    mig = [c for c in mod.calls if c.name.startswith("op.") and c.attr in ("create_table", "add_column", "create_index", "create_foreign_key", "alter_column")]
    if mig:
        out.append(Finding(
            skill_key="sql", detector="alembic_migration", start_line=mig[0].line, end_line=min(mig[-1].end_line, mig[0].line + 40),
            node_type="schema_migration", signal_level="code_usage", depth=depth_from_size(len(mig), 1, 2, 6), source_type="CODE_IMPLEMENTATION",
            reason=f"Schema migration with {len(mig)} operations ({', '.join(sorted({m.attr for m in mig}))}).",
        ))
    # SQL literals that are never executed -> ambiguous
    amb = 0
    for line, end, text, func in mod.sql_strings:
        if line in executed_lines or any(ln in executed_lines for ln in range(line, end + 1)) or line in mod.docstring_lines:
            continue
        if amb >= 2:
            break
        amb += 1
        out.append(Finding(
            skill_key="sql", detector="py_sql_literal", start_line=line, end_line=min(end, line + 15), node_type="sql_string_literal",
            signal_level="code_usage", depth="basic", source_type="CODE_IMPLEMENTATION",
            reason="SQL text appears in code but no execution call using it was observed in this file.",
            candidate_status="ambiguous", symbol=func,
        ))
    return out[:14]


def _meaningful_asserts(f: PyFunc) -> tuple[int, int, list[str]]:
    asserts = 0
    trivial = 0
    notes: list[str] = []
    for n in ast.walk(f.node):
        if isinstance(n, ast.Assert):
            t = n.test
            if isinstance(t, ast.Constant):
                trivial += 1
                continue
            if isinstance(t, ast.Compare) and len(t.comparators) == 1 and ast.dump(t.left) == ast.dump(t.comparators[0]):
                trivial += 1
                continue
            if isinstance(t, ast.Compare) and isinstance(t.left, ast.Constant) and all(isinstance(c, ast.Constant) for c in t.comparators):
                trivial += 1
                continue
            asserts += 1
        elif isinstance(n, ast.Call):
            name = dotted(n.func)
            attr = name.split(".")[-1]
            if attr.startswith("assert") or attr in ("fail", "expect") or attr.startswith("assert_called") or attr == "assert_awaited":
                if n.args and all(isinstance(a, ast.Constant) for a in n.args) and attr in ("assertTrue", "assertFalse", "assertEqual", "assertIsNotNone"):
                    trivial += 1
                    continue
                asserts += 1
            if name in ("pytest.raises", "self.assertRaises", "pytest.warns"):
                asserts += 1
                notes.append("checks raised exceptions")
    return asserts, trivial, notes


def detect_tests(mod: PyModule, path: str, n_lines: int) -> list[Finding]:
    out: list[Finding] = []
    test_classes = {c.name for c in mod.classes if c.name.startswith("Test") or any(b.split(".")[-1] in ("TestCase", "IsolatedAsyncioTestCase", "APITestCase", "TransactionTestCase") for b in c.bases)}
    tests = [f for f in mod.funcs if f.name.startswith("test") and (f.class_name is None or f.class_name in test_classes)]
    if not tests:
        if path.split("/")[-1] != "conftest.py":
            out.append(Finding(
                skill_key="automated_testing", detector="py_test_function", start_line=1, end_line=max(1, min(n_lines, 20)),
                node_type="test_file", signal_level="tests_found", depth="trivial", source_type="CODE_TEST",
                reason="Test file contains no test functions.", candidate_status="rejected",
            ))
        return out
    accepted = 0
    for f in tests:
        asserts, trivial, notes = _meaningful_asserts(f)
        sut_calls = [c for c in mod.calls if c.func == f.qualname and c.attr not in NOT_SUT_CALLS and not c.attr.startswith("assert")]
        if f.trivial or asserts == 0:
            why = "empty/placeholder test body" if f.trivial else ("only constant assertions such as `assert True`" if trivial else "no assertions")
            out.append(Finding(
                skill_key="automated_testing", detector="py_test_function", start_line=f.line, end_line=min(f.end_line, f.line + 30),
                node_type="test_function", signal_level="tests_found", depth="trivial", source_type="CODE_TEST",
                reason=f"`{f.qualname}`: {why} — not adequate testing evidence.", candidate_status="rejected", symbol=f.qualname,
            ))
            continue
        if not sut_calls:
            notes.append("does not call any code under test")
        score = asserts + min(3, len(sut_calls))
        depth = "intermediate" if score >= 3 and sut_calls else "basic"
        if asserts >= 3 and len(sut_calls) >= 2:
            depth = "substantial"
        accepted += 1
        if accepted > 6:
            continue
        out.append(Finding(
            skill_key="automated_testing", detector="py_test_function", start_line=f.line, end_line=min(f.end_line, f.line + 35),
            node_type="test_function", signal_level="tests_found", depth=depth, source_type="CODE_TEST",
            reason=f"`{f.qualname}` makes {asserts} meaningful assertion(s) and {len(sut_calls)} call(s) to code under test" + (f" ({', '.join(notes)})" if notes else "") + ".",
            candidate_status="accepted" if sut_calls else "ambiguous", symbol=f.qualname,
            meta={"assertions": asserts, "sut_calls": len(sut_calls)},
        ))
    return out


def detect_auth(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str | None] = set()
    for call in mod.calls:
        n = call.name
        hit = None
        if n in ("jwt.encode", "jwt.decode") or n.endswith("jwt.encode") or n.endswith("jwt.decode"):
            hit = "JWT token handling"
        elif call.attr in ("hashpw", "checkpw") or n in ("generate_password_hash", "check_password_hash") or n.endswith("pwd_context.hash") or n.endswith("pwd_context.verify"):
            hit = "password hashing/verification"
        elif call.attr in ("OAuth2PasswordBearer", "HTTPBearer", "OAuth2PasswordRequestForm", "APIKeyHeader") or n in ("login_user", "logout_user", "authenticate"):
            hit = "authentication scheme"
        elif call.attr == "CryptContext":
            hit = "password hashing context"
        if not hit:
            continue
        f = func_by_qualname(mod, call.func) or enclosing_func(mod, call.line)
        key = f.qualname if f else f"line{call.line}"
        if key in seen:
            continue
        seen.add(key)
        s, e = _snippet_range(f, call.line, call.end_line, cap=30)
        depth = "intermediate" if f and f.n_statements >= 3 else "basic"
        out.append(Finding(
            skill_key="auth_security", detector="py_auth", start_line=s, end_line=e, node_type="auth_call",
            signal_level="meaningful_implementation" if depth == "intermediate" else "code_usage", depth=depth,
            source_type="CODE_IMPLEMENTATION", reason=f"{hit} via `{n}` in `{f.qualname if f else 'module scope'}`.",
            symbol=f.qualname if f else None,
        ))
    for f in mod.funcs:
        for dname, _, dline in f.decorators:
            if dname.split(".")[-1] in ("login_required", "permission_required", "jwt_required", "requires_auth", "user_passes_test"):
                out.append(Finding(
                    skill_key="auth_security", detector="py_auth_guard", start_line=dline, end_line=min(f.end_line, f.line + 20),
                    node_type="auth_decorator", signal_level="code_usage", depth="basic", source_type="CODE_IMPLEMENTATION",
                    reason=f"Endpoint `{f.qualname}` protected by `@{dname}`.", symbol=f.qualname,
                ))
                break
    return out[:6]


def detect_caching(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    for f in mod.funcs:
        for dname, _, dline in f.decorators:
            if dname.split(".")[-1] in ("lru_cache", "cache", "cached", "cached_property", "memoize", "cache_page", "cacheable"):
                out.append(Finding(
                    skill_key="caching", detector="py_cache_decorator", start_line=dline, end_line=min(f.end_line, f.line + 20),
                    node_type="cache_decorator", signal_level="code_usage", depth="basic", source_type="CODE_IMPLEMENTATION",
                    reason=f"`{f.qualname}` memoised with `@{dname}`.", symbol=f.qualname,
                ))
    redis_aliases = {k for k, v in mod.imports.items() if v.split(".")[0] in ("redis", "aioredis", "cachetools", "diskcache", "pymemcache")}
    rcalls = [c for c in mod.calls if c.name.split(".")[0] in redis_aliases or c.attr in ("setex", "expire", "set_ex")]
    if rcalls and redis_aliases:
        f = enclosing_func(mod, rcalls[0].line)
        expiring = any(c.attr in ("setex", "expire") or "ex" in c.kw_names for c in mod.calls)
        s, e = _snippet_range(f, rcalls[0].line, rcalls[-1].end_line, cap=30)
        out.append(Finding(
            skill_key="caching", detector="py_cache_client", start_line=s, end_line=e, node_type="cache_client",
            signal_level="meaningful_implementation" if expiring else "code_usage", depth="intermediate" if expiring else "basic",
            source_type="CODE_IMPLEMENTATION", reason=f"Cache client used ({', '.join(sorted(redis_aliases))}){' with expiry' if expiring else ''}.",
            symbol=f.qualname if f else None,
        ))
    return out[:4]


CONC_CALLS = {"asyncio.gather": 2, "asyncio.create_task": 2, "asyncio.wait": 2, "asyncio.Semaphore": 2, "asyncio.Lock": 2, "asyncio.Queue": 2,
              "asyncio.TaskGroup": 2, "threading.Thread": 2, "threading.Lock": 2, "ThreadPoolExecutor": 2, "ProcessPoolExecutor": 2,
              "multiprocessing.Pool": 2, "multiprocessing.Process": 2, "anyio.create_task_group": 2, "concurrent.futures.ThreadPoolExecutor": 2}


def detect_concurrency(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str | None] = set()
    for call in mod.calls:
        resolved = call.name
        root = call.name.split(".")[0]
        if root in mod.imports and "." not in call.name:
            resolved = mod.imports[root]
        if not any(resolved.endswith(k) for k in CONC_CALLS):
            continue
        f = func_by_qualname(mod, call.func) or enclosing_func(mod, call.line)
        key = f.qualname if f else None
        if key in seen:
            continue
        seen.add(key)
        s, e = _snippet_range(f, call.line, call.end_line, cap=30)
        out.append(Finding(
            skill_key="concurrency", detector="py_concurrency", start_line=s, end_line=e, node_type="concurrency_primitive",
            signal_level="meaningful_implementation", depth="intermediate" if f and f.n_statements >= 4 else "basic",
            source_type="CODE_IMPLEMENTATION", reason=f"Concurrency primitive `{call.name}` used in `{key or 'module scope'}`.", symbol=key,
        ))
    return out[:4]


def detect_observability(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    special = [c for c in mod.calls if c.name.startswith(("sentry_sdk.init", "structlog.", "prometheus_client.", "Counter", "Histogram", "trace.get_tracer"))]
    logs = [c for c in mod.calls if c.attr in ("info", "warning", "error", "exception", "debug", "critical") and c.name.split(".")[0] in ("logger", "log", "logging", "self.logger")]
    if special:
        c = special[0]
        out.append(Finding("observability", "py_observability", c.line, c.end_line + 5, "telemetry_setup", "code_usage", "intermediate",
                           "CODE_IMPLEMENTATION", f"Telemetry/monitoring via `{c.name}`."))
    elif len(logs) >= 3 and any(c.attr == "getLogger" for c in mod.calls):
        c = logs[0]
        out.append(Finding("observability", "py_logging", c.line, c.end_line, "structured_logging", "code_usage", "basic",
                           "CODE_IMPLEMENTATION", f"Module logger with {len(logs)} log calls."))
    return out


def detect_oop(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    local = {c.name for c in mod.classes}
    for c in mod.classes:
        methods = [m for m in c.methods if not (m.startswith("__") and m.endswith("__"))]
        if len(methods) < 2 or c.name.startswith("Test"):
            continue
        inherits = [b for b in c.bases if b in local or b.split(".")[-1] in ("ABC", "Protocol", "Generic")]
        depth = "intermediate" if inherits or len(methods) >= 4 else "basic"
        out.append(Finding("oop", "py_class_design", c.line, min(c.end_line, c.line + 40), "class_definition", "code_usage", depth,
                           "CODE_IMPLEMENTATION", f"Class `{c.name}` with {len(methods)} methods" + (f", extends {', '.join(inherits)}" if inherits else "") + ".",
                           symbol=c.name))
    return sorted(out, key=lambda f: -len(f.reason))[:3]


def detect_libraries(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    for lib in LIBS:
        if not lib.py:
            continue
        aliases = {local for local, target in mod.imports.items() if any(target == m or target.startswith(m + ".") for m in lib.py)}
        if not aliases:
            continue
        usage = [c for c in mod.calls if c.name and (c.name.split(".")[0] in aliases)]
        deco_usage = [(f, d) for f in mod.funcs for d in f.decorators if d[0].split(".")[0] in aliases]
        count = len(usage) + len(deco_usage) + sum(mod.names_used.get(a, 0) for a in aliases if not any(c.name.split(".")[0] == a for c in usage))
        if count == 0:
            continue
        first_line = min([c.line for c in usage] + [d[2] for _, d in deco_usage] + [mod.import_lines.get(a, 10**9) for a in aliases])
        f = enclosing_func(mod, usage[0].line) if usage else None
        s, e = _snippet_range(f, first_line, first_line + 12, cap=30)
        depth = depth_from_size(count, 1, 3, 10)
        out.append(Finding(
            skill_key=lib.skill_key, detector=f"py_import_usage:{lib.key}", start_line=s, end_line=e, node_type="library_usage",
            signal_level="meaningful_implementation" if depth == "substantial" else "code_usage", depth=depth, source_type="CODE_IMPLEMENTATION",
            reason=f"{lib.label} imported ({', '.join(sorted(aliases))}) and used {count} time(s) in this file.",
            meta={"usage_count": count},
        ))
    return out


def detect_cloud(mod: PyModule, path: str) -> list[Finding]:
    out: list[Finding] = []
    for skill, prefixes in (("aws", ("boto3", "botocore", "aws_cdk", "aws_lambda_powertools")), ("gcp", ("google.cloud", "google.auth", "firebase_admin")),
                            ("azure", ("azure.",))):
        aliases = {local for local, target in mod.imports.items() if target.startswith(prefixes) or target in tuple(p.rstrip(".") for p in prefixes)}
        usage = [c for c in mod.calls if c.name.split(".")[0] in aliases]
        if aliases and usage:
            c = usage[0]
            f = enclosing_func(mod, c.line)
            s, e = _snippet_range(f, c.line, c.end_line, cap=25)
            out.append(Finding(skill, "py_cloud_sdk", s, e, "cloud_sdk_call", "code_usage", depth_from_size(len(usage), 1, 3, 8),
                               "CODE_IMPLEMENTATION", f"{skill.upper()} SDK used via `{c.name}` ({len(usage)} call(s))."))
            out.append(Finding("cloud", "py_cloud_sdk", s, e, "cloud_sdk_call", "code_usage", depth_from_size(len(usage), 1, 3, 8),
                               "CODE_IMPLEMENTATION", f"{skill.upper()} SDK used via `{c.name}`."))
    return out


def language_finding(mod: PyModule, path: str, code_lines: int) -> Finding | None:
    defs = [f for f in mod.funcs if not f.trivial]
    if not defs and not mod.classes:
        return None
    largest = max(defs, key=lambda f: f.end_line - f.line) if defs else None
    s, e = (largest.line, min(largest.end_line, largest.line + 40)) if largest else (mod.classes[0].line, mod.classes[0].line + 20)
    depth = "substantial" if code_lines >= 150 and len(defs) >= 5 else "intermediate" if code_lines >= 50 and len(defs) >= 2 else "basic"
    return Finding("python", "py_language_usage", s, e, "module", "code_usage", depth, "CODE_IMPLEMENTATION",
                   f"Python module with {len(defs)} non-trivial function(s), {len(mod.classes)} class(es), {code_lines} code lines.",
                   symbol=largest.qualname if largest else None, meta={"code_lines": code_lines, "defs": len(defs)})


def scan_python(path: str, text: str, kind: str) -> tuple[list[Finding], PyModule]:
    mod = parse_python(text)
    findings: list[Finding] = []
    if not mod.ok:
        return findings, mod
    n_lines = len(mod.lines)
    code_lines = sum(1 for i, ln in enumerate(mod.lines, 1) if ln.strip() and not ln.strip().startswith("#") and i not in mod.docstring_lines)
    if kind == "test":
        findings += detect_tests(mod, path, n_lines)
        findings += [f for f in detect_libraries(mod, path) if f.skill_key not in ("fastapi", "flask", "django")]
    else:
        findings += detect_api(mod, path)
        findings += detect_sql(mod, path)
        findings += detect_auth(mod, path)
        findings += detect_caching(mod, path)
        findings += detect_concurrency(mod, path)
        findings += detect_observability(mod, path)
        findings += detect_oop(mod, path)
        findings += detect_libraries(mod, path)
        findings += detect_cloud(mod, path)
        lf = language_finding(mod, path, code_lines)
        if lf:
            findings.append(lf)
    for f in findings:
        f.clamp(n_lines)
    return findings, mod
