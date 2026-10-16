"""Detectors for JS/TS, Java, Go, Kotlin, C#, Rust, PHP, Ruby.

Code is comment-stripped first (line numbers preserved), so routes/SQL/tests written in
comments never match. These are lexical detectors with bracket matching, not full
parsers; the README documents that coverage is shallower than for Python.
"""
from __future__ import annotations

import re

from .findings import Finding, depth_from_size
from .lexer import line_of, match_block, strip_c_comments, strip_hash_comments
from .libraries import LIBS, lib_for_js

SQL_KW = r"(SELECT\s[\s\S]{0,200}?\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE|WITH\s+\w+\s+AS)"
ROUTER_OBJ = re.compile(r"^(app|router|server|fastify|api|apiRouter|routes|route|r|v1|\w*[Rr]outer|\w*[Aa]pp)$")
CLIENT_OBJ = {"axios", "fetch", "http", "https", "request", "client", "instance", "supertest", "agent", "api", "$http", "ky", "got", "superagent"}


def _block_lines(code: str, start_idx: int, open_ch: str = "(", close_ch: str = ")", cap: int = 45) -> tuple[int, int, str]:
    i = code.find(open_ch, start_idx)
    if i < 0:
        ln = line_of(code, start_idx)
        return ln, ln, ""
    j = match_block(code, i, open_ch, close_ch)
    s = line_of(code, start_idx)
    e = line_of(code, j) if j > 0 else s
    body = code[i:j + 1] if j > 0 else code[i:i + 400]
    return s, min(e, s + cap), body


def _stmt_count(body: str) -> int:
    return sum(1 for ln in body.split("\n") if ln.strip() and ln.strip() not in ("{", "}", "});", "})", ")", "};", "(", "),"))


# ------------------------------------------------------------------ JS / TS


def _js_imports(code: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    for m in re.finditer(r"import\s+(type\s+)?([\w$]+)?\s*,?\s*(\{([^}]*)\}|\*\s+as\s+([\w$]+))?\s*from\s*['\"]([^'\"]+)['\"]", code):
        mod = m.group(6)
        if m.group(2):
            imports[m.group(2)] = mod
        if m.group(5):
            imports[m.group(5)] = mod
        if m.group(4):
            for part in m.group(4).split(","):
                part = part.strip()
                if not part:
                    continue
                local = part.split(" as ")[-1].strip().replace("type ", "")
                imports[local] = mod
    for m in re.finditer(r"(?:const|let|var)\s+(\{[^}]*\}|[\w$]+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", code):
        tgt, mod = m.group(1), m.group(2)
        if tgt.startswith("{"):
            for part in tgt.strip("{} ").split(","):
                part = part.strip()
                if part:
                    imports[part.split(":")[-1].strip()] = mod
        else:
            imports[tgt] = mod
    for m in re.finditer(r"^\s*import\s+['\"]([^'\"]+)['\"]", code, re.M):
        imports[f"__side_effect__{m.group(1)}"] = m.group(1)
    return imports


def _usage_count(code: str, name: str) -> int:
    if name.startswith("__side_effect__"):
        return 1
    hits = len(re.findall(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])", code))
    return max(0, hits - 1)  # minus the import itself


def _js_api(code: str, path: str) -> list[Finding]:
    out: list[Finding] = []
    for m in re.finditer(r"\b([\w$]+)\.(get|post|put|patch|delete|all|options|head)\s*\(\s*(['\"`])(/[^'\"`]*)\3", code):
        obj = m.group(1)
        if obj in CLIENT_OBJ or not ROUTER_OBJ.match(obj):
            continue
        s, e, body = _block_lines(code, m.start())
        n = _stmt_count(body)
        err = bool(re.search(r"\btry\b|\.catch\(|status\(\s*[45]\d\d|next\(\s*\w+|throw\s", body))
        depth = "trivial" if n <= 1 else "basic" if n <= 3 and not err else "intermediate" if n < 10 else "substantial"
        out.append(Finding("api_development", "js_route_registration", s, e, "route_registration",
                           "meaningful_implementation" if depth in ("intermediate", "substantial") else "code_usage", depth, "CODE_IMPLEMENTATION",
                           f"{m.group(2).upper()} {m.group(4)} registered on `{obj}` ({n} handler lines{', error handling' if err else ''}).",
                           candidate_status="rejected" if depth == "trivial" else "accepted", meta={"route": m.group(4)}))
    low = path.lower()
    nextjs_route = re.search(r"(^|/)app/(.+/)?route\.[cm]?[jt]sx?$", low) or "/pages/api/" in "/" + low
    if nextjs_route:
        for m in re.finditer(r"export\s+(async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|handler)\b|export\s+default\s+(async\s+)?function|export\s+const\s+(GET|POST|PUT|PATCH|DELETE)\s*=", code):
            s, e, body = _block_lines(code, m.start(), "{", "}")
            n = _stmt_count(body)
            depth = "basic" if n <= 3 else "intermediate" if n < 12 else "substantial"
            out.append(Finding("api_development", "nextjs_route_handler", s, e, "route_handler",
                               "meaningful_implementation" if depth != "basic" else "code_usage", depth, "CODE_IMPLEMENTATION",
                               f"Next.js API route handler ({m.group(2) or m.group(4) or 'default export'}) with {n} lines."))
    if "@Controller(" in code:
        for m in re.finditer(r"@(Get|Post|Put|Patch|Delete)\(\s*(['\"`][^'\"`]*['\"`])?\s*\)", code):
            s, e, body = _block_lines(code, m.end(), "{", "}")
            out.append(Finding("api_development", "nestjs_route", line_of(code, m.start()), e, "controller_method", "code_usage",
                               depth_from_size(_stmt_count(body), 1, 4, 10), "CODE_IMPLEMENTATION", f"NestJS @{m.group(1)} controller method."))
    return out[:12]


def _js_sql(code: str, path: str) -> list[Finding]:
    out: list[Finding] = []
    exec_spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\.(query|execute|raw|unsafe|run|all|get|prepare|\$queryRaw|\$executeRaw|\$queryRawUnsafe)\s*(\(|`)\s*(['\"`])?\s*" + SQL_KW, code, re.I):
        s, e, body = _block_lines(code, m.start())
        exec_spans.append((m.start(), m.end()))
        param = bool(re.search(r"\?|\$\d|:\w+|,\s*\[", body))
        out.append(Finding("sql", "js_sql_execution", s, max(e, s + 3), "sql_execute_call", "meaningful_implementation",
                           "intermediate" if param else "basic", "CODE_IMPLEMENTATION",
                           f"Executes SQL via `.{m.group(1)}(...)`{' with bound parameters' if param else ''}."))
    for m in re.finditer(r"\bsql\s*`\s*" + SQL_KW, code, re.I):
        exec_spans.append((m.start(), m.end()))
        s = line_of(code, m.start())
        out.append(Finding("sql", "js_sql_tagged_template", s, s + 8, "sql_tagged_template", "meaningful_implementation", "intermediate",
                           "CODE_IMPLEMENTATION", "SQL tagged-template query (parameterised by the driver)."))
    for m in re.finditer(r"\bprisma\.(\w+)\.(findMany|findUnique|findFirst|create|createMany|update|updateMany|upsert|delete|deleteMany|count|aggregate|groupBy)\s*\(", code):
        s, e, body = _block_lines(code, m.start())
        out.append(Finding("sql", "prisma_query", s, e, "orm_query", "meaningful_implementation", "intermediate", "CODE_IMPLEMENTATION",
                           f"Prisma ORM `{m.group(1)}.{m.group(2)}` query."))
    for m in re.finditer(r"\b(knex|db)\s*\(\s*['\"]\w+['\"]\s*\)\s*\.(select|where|insert|update|del|delete|join|first)\b|\.createQueryBuilder\(|\bgetRepository\(|\bdb\.select\(\)\.from\(|\bdb\.insert\(\w+\)", code):
        s, e, _ = _block_lines(code, m.start())
        out.append(Finding("sql", "js_query_builder", s, max(e, s + 4), "orm_query", "meaningful_implementation", "intermediate", "CODE_IMPLEMENTATION",
                           "Query-builder/ORM database operation."))
    for m in re.finditer(r"@Entity\(|\bsequelize\.define\(|\.init\(\s*\{[\s\S]{0,300}?DataTypes\.|\b(pgTable|sqliteTable|mysqlTable)\(\s*['\"]\w+['\"]", code):
        s = line_of(code, m.start())
        out.append(Finding("sql", "js_orm_model", s, s + 25, "orm_model", "code_usage", "basic", "CODE_IMPLEMENTATION", "ORM entity/table schema definition."))
    lit = 0
    for m in re.finditer(r"(['\"`])\s*" + SQL_KW, code, re.I):
        if any(a <= m.start() <= b + 5 for a, b in exec_spans) or lit >= 2:
            continue
        lit += 1
        s = line_of(code, m.start())
        out.append(Finding("sql", "js_sql_literal", s, s + 6, "sql_string_literal", "code_usage", "basic", "CODE_IMPLEMENTATION",
                           "SQL text in a string, but no execution call using it was observed nearby.", candidate_status="ambiguous"))
    # de-duplicate by start line
    seen: set[int] = set()
    uniq = []
    for f in out:
        if f.start_line in seen:
            continue
        seen.add(f.start_line)
        uniq.append(f)
    return uniq[:12]


def _js_tests(code: str, path: str) -> list[Finding]:
    out: list[Finding] = []
    found = 0
    for m in re.finditer(r"(?<![\w.])(it|test)(\.only)?\s*\(\s*(['\"`])(.*?)\3", code):
        found += 1
        s, e, body = _block_lines(code, m.start(), cap=40)
        trivial_exp = len(re.findall(r"expect\(\s*(true|false|1|0|null|['\"][^'\"]*['\"])\s*\)\s*\.\s*to(Be|Equal|StrictEqual)\(\s*(true|false|1|0|null|['\"][^'\"]*['\"])\s*\)", body))
        expects = len(re.findall(r"\bexpect\s*\(|\bassert(\.\w+)?\s*\(|\.should\.|\.to\.(be|equal|have)", body)) - trivial_exp
        calls = len(re.findall(r"\b(await\s+)?[\w$.]+\s*\(", body)) - len(re.findall(r"\bexpect\s*\(", body))
        if expects <= 0:
            out.append(Finding("automated_testing", "js_test_case", s, e, "test_case", "tests_found", "trivial", "CODE_TEST",
                               f"Test '{m.group(4)[:60]}' has no meaningful assertion" + (" (only constant expectations)" if trivial_exp else "") + ".",
                               candidate_status="rejected"))
            continue
        depth = "substantial" if expects >= 3 and calls >= 3 else "intermediate" if expects >= 2 or calls >= 3 else "basic"
        if len([f for f in out if f.candidate_status == "accepted"]) < 6:
            out.append(Finding("automated_testing", "js_test_case", s, e, "test_case", "tests_found", depth, "CODE_TEST",
                               f"Test '{m.group(4)[:60]}' with {expects} assertion(s).", meta={"assertions": expects}))
    if found == 0:
        out.append(Finding("automated_testing", "js_test_case", 1, min(20, code.count("\n") + 1), "test_file", "tests_found", "trivial", "CODE_TEST",
                           "Test file contains no test cases.", candidate_status="rejected"))
    return out


def _generic_js_patterns(code: str) -> list[Finding]:
    out: list[Finding] = []
    rules = [
        ("auth_security", r"\bjwt\.(sign|verify)\s*\(|\bbcrypt(js)?\.(hash|compare)(Sync)?\s*\(|\bpassport\.use\s*\(|\bNextAuth\s*\(|\bgetServerSession\s*\(|\bargon2\.(hash|verify)\s*\(|\bhelmet\s*\(", "js_auth", "auth_call"),
        ("caching", r"\bcreateClient\s*\([\s\S]{0,80}?\)|new\s+(Redis|NodeCache|LRUCache)\s*\(|\.setex\s*\(|['\"]Cache-Control['\"]", "js_cache", "cache_usage"),
        ("concurrency", r"\bPromise\.(all|allSettled|race|any)\s*\(|new\s+Worker\s*\(|\bpLimit\s*\(", "js_concurrency", "concurrency_primitive"),
        ("observability", r"\b(winston\.createLogger|pino\s*\(|Sentry\.init|new\s+client\.(Counter|Histogram)|NodeSDK\s*\()", "js_observability", "telemetry_setup"),
    ]
    for skill, pat, det, node in rules:
        ms = list(re.finditer(pat, code))
        if not ms:
            continue
        s, e, body = _block_lines(code, ms[0].start(), cap=25)
        out.append(Finding(skill, det, s, max(e, s + 2), node, "code_usage", depth_from_size(len(ms), 1, 3, 6), "CODE_IMPLEMENTATION",
                           f"`{ms[0].group(0).strip()[:50]}` used ({len(ms)} occurrence(s)) in this file."))
    classes = list(re.finditer(r"\bclass\s+(\w+)(\s+extends\s+\w+|\s+implements\s+[\w, ]+)?\s*\{", code))
    if classes:
        rich = [c for c in classes if c.group(2)]
        c = (rich or classes)[0]
        s, e, body = _block_lines(code, c.start(), "{", "}", cap=40)
        methods = len(re.findall(r"^\s*(public|private|protected|async|static|get|set)?\s*\w+\s*\([^)]*\)\s*(:\s*[\w<>\[\]| ]+)?\s*\{", body, re.M))
        if methods >= 2 or c.group(2):
            out.append(Finding("oop", "js_class_design", s, e, "class_definition", "code_usage", "intermediate" if c.group(2) else "basic",
                               "CODE_IMPLEMENTATION", f"Class `{c.group(1)}`{c.group(2) or ''} with {methods} methods."))
    return out


def _js_libraries(code: str, path: str, imports: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    by_lib: dict[str, tuple[int, list[str]]] = {}
    for local, mod in imports.items():
        for lib in lib_for_js(mod):
            cnt = _usage_count(code, local)
            if lib.key == "react" and re.search(r"<[A-Z]\w*[\s/>]|return\s*\(\s*<", code):
                cnt += 2
            tot, names = by_lib.get(lib.key, (0, []))
            by_lib[lib.key] = (tot + cnt, names + [local.replace("__side_effect__", "")])
    for key, (count, names) in by_lib.items():
        lib = next(l for l in LIBS if l.key == key)
        if count <= 0:
            continue
        first = min((m.start() for n in names for m in re.finditer(r"(?<![\w$])" + re.escape(n) + r"(?![\w$])", code)), default=0)
        s = line_of(code, first)
        depth = depth_from_size(count, 1, 3, 10)
        out.append(Finding(lib.skill_key, f"js_import_usage:{lib.key}", s, s + 15, "library_usage",
                           "meaningful_implementation" if depth == "substantial" else "code_usage", depth, "CODE_IMPLEMENTATION",
                           f"{lib.label} imported ({', '.join(sorted(set(names))[:4])}) and used {count} time(s) in this file.", meta={"usage_count": count}))
    for skill, prefixes in (("aws", ("@aws-sdk/", "aws-sdk", "aws-cdk-lib")), ("gcp", ("@google-cloud/", "firebase-admin")), ("azure", ("@azure/",))):
        locs = [l for l, m in imports.items() if m.startswith(prefixes)]
        cnt = sum(_usage_count(code, l) for l in locs)
        if cnt > 0:
            first = min((m.start() for l in locs for m in re.finditer(re.escape(l), code)), default=0)
            s = line_of(code, first)
            for sk in (skill, "cloud"):
                out.append(Finding(sk, "js_cloud_sdk", s, s + 12, "cloud_sdk_usage", "code_usage", depth_from_size(cnt, 1, 3, 8), "CODE_IMPLEMENTATION",
                                   f"{skill.upper()} SDK used ({cnt} reference(s))."))
    return out


def _language_finding(lang: str, code: str, def_pat: str, skill: str) -> Finding | None:
    defs = list(re.finditer(def_pat, code, re.M))
    code_lines = sum(1 for ln in code.split("\n") if ln.strip())
    if not defs or code_lines < 10:
        return None
    best = defs[0]
    best_len = 0
    for d in defs[:60]:
        i = code.find("{", d.end() - 1)
        j = match_block(code, i, "{", "}") if i >= 0 else -1
        ln = (j - i) if j > 0 else 0
        if ln > best_len:
            best, best_len = d, ln
    s, e, _ = _block_lines(code, best.start(), "{", "}", cap=40)
    depth = "substantial" if code_lines >= 150 and len(defs) >= 5 else "intermediate" if code_lines >= 50 and len(defs) >= 2 else "basic"
    return Finding(skill, f"{lang}_language_usage", s, max(e, s + 1), "source_file", "code_usage", depth, "CODE_IMPLEMENTATION",
                   f"{lang} source with {len(defs)} definition(s) and {code_lines} code lines.", meta={"code_lines": code_lines, "defs": len(defs)})


def scan_js(path: str, text: str, kind: str, lang: str) -> list[Finding]:
    code = strip_c_comments(text)
    imports = _js_imports(code)
    out: list[Finding] = []
    if kind == "test":
        out += _js_tests(code, path)
    else:
        out += _js_api(code, path)
        out += _js_sql(code, path)
        out += _generic_js_patterns(code)
        lf = _language_finding(lang, code, r"\bfunction\s+\w+\s*\(|\b(const|let)\s+\w+\s*=\s*(async\s*)?\([^)]*\)\s*(:\s*[\w<>\[\]| ]+)?\s*=>|\bclass\s+\w+|^\s*(export\s+)?(default\s+)?(async\s+)?\w+\s*\([^)]*\)\s*\{", "typescript" if lang == "typescript" else "javascript")
        if lf:
            out.append(lf)
    out += _js_libraries(code, path, imports)
    if path.endswith((".jsx", ".tsx")) or (re.search(r"return\s*\(\s*<|=>\s*\(\s*<", code) and "react" in " ".join(imports.values())):
        pass
    n = text.count("\n") + 1
    return [f.clamp(n) for f in out]


# ------------------------------------------------------------------ Java / Kotlin


def scan_java(path: str, text: str, kind: str, lang: str) -> list[Finding]:
    code = strip_c_comments(text)
    out: list[Finding] = []
    if kind == "test" or re.search(r"@Test\b", code):
        for m in re.finditer(r"@(Test|ParameterizedTest)\b[\s\S]{0,200}?(void|fun)\s+(\w+)\s*\(", code):
            s, e, body = _block_lines(code, m.end(), "{", "}", cap=40)
            trivial = len(re.findall(r"assert(True|Equals)\(\s*(true|1\s*,\s*1)\s*\)", body))
            asserts = len(re.findall(r"\bassert\w*\s*\(|\bassertThat\s*\(|\bverify\s*\(|\bexpect\w*\s*\(|\bshouldBe\b", body)) - trivial
            out.append(Finding("automated_testing", f"{lang}_junit_test", line_of(code, m.start()), e, "test_method", "tests_found",
                               "trivial" if asserts <= 0 else depth_from_size(asserts, 1, 2, 4), "CODE_TEST",
                               f"Test method `{m.group(3)}` with {max(0, asserts)} assertion(s).", candidate_status="rejected" if asserts <= 0 else "accepted"))
        return [f.clamp(text.count("\n") + 1) for f in out[:8]]
    if re.search(r"@(Rest)?Controller\b", code):
        for m in re.finditer(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b(\(([^)]*)\))?", code):
            s, e, body = _block_lines(code, m.end(), "{", "}", cap=40)
            n = _stmt_count(body)
            depth = "basic" if n <= 3 else "intermediate" if n < 10 else "substantial"
            out.append(Finding("api_development", "spring_mapping", line_of(code, m.start()), e, "controller_method",
                               "meaningful_implementation" if depth != "basic" else "code_usage", depth, "CODE_IMPLEMENTATION",
                               f"Spring @{m.group(1)}Mapping{m.group(2) or ''} handler ({n} lines)."))
    for pat, det, node, sig, depth, why in (
        (r"@Entity\b", "jpa_entity", "orm_model", "code_usage", "basic", "JPA @Entity mapped to a table."),
        (r"@Query\s*\(", "jpa_query", "orm_query", "meaningful_implementation", "intermediate", "Custom JPA @Query."),
        (r"\b(jdbcTemplate|namedParameterJdbcTemplate)\.(query|update|queryForObject|queryForList|batchUpdate)\s*\(", "jdbc_template", "sql_execute_call", "meaningful_implementation", "intermediate", "JdbcTemplate SQL execution."),
        (r"\.prepareStatement\s*\(", "jdbc_prepared", "sql_execute_call", "meaningful_implementation", "intermediate", "JDBC prepared statement."),
        (r"extends\s+(JpaRepository|CrudRepository|PagingAndSortingRepository)\s*<", "spring_data_repo", "orm_repository", "code_usage", "basic", "Spring Data repository interface."),
        (r"\bSecurityFilterChain\b|@PreAuthorize\b|BCryptPasswordEncoder|\bJwts\.", "spring_security", "auth_config", "code_usage", "intermediate", "Spring Security / JWT configuration."),
        (r"\bExecutorService\b|\bCompletableFuture\b|\bsynchronized\s*\(|@Async\b|\bConcurrentHashMap\b|\blaunch\s*\{|\basync\s*\{", "jvm_concurrency", "concurrency_primitive", "code_usage", "intermediate", "JVM concurrency primitive."),
        (r"@Cacheable\b|@EnableCaching\b|RedisTemplate", "spring_cache", "cache_usage", "code_usage", "basic", "Spring caching."),
    ):
        m = re.search(pat, code)
        if m:
            skill = {"jpa_entity": "sql", "jpa_query": "sql", "jdbc_template": "sql", "jdbc_prepared": "sql", "spring_data_repo": "sql",
                     "spring_security": "auth_security", "jvm_concurrency": "concurrency", "spring_cache": "caching"}[det]
            s, e, _ = _block_lines(code, m.start(), "{", "}", cap=30)
            out.append(Finding(skill, det, line_of(code, m.start()), max(e, line_of(code, m.start()) + 3), node, sig, depth, "CODE_IMPLEMENTATION", why))
    if re.search(r"^\s*import\s+org\.springframework", code, re.M):
        m = re.search(r"^\s*import\s+org\.springframework", code, re.M)
        uses = len(re.findall(r"@(RestController|Service|Component|Repository|Autowired|Bean|Configuration|SpringBootApplication|GetMapping|PostMapping)\b", code))
        if uses:
            s = line_of(code, m.start())
            out.append(Finding("spring", "spring_usage", s, s + 20, "library_usage", "code_usage", depth_from_size(uses, 1, 3, 8), "CODE_IMPLEMENTATION",
                               f"Spring annotations used {uses} time(s)."))
    cls = re.search(r"\bclass\s+(\w+)\s+(extends|implements|:)\s*[\w<>, ]+", code)
    if cls:
        s, e, _ = _block_lines(code, cls.start(), "{", "}", cap=40)
        out.append(Finding("oop", f"{lang}_class_design", s, e, "class_definition", "code_usage", "intermediate", "CODE_IMPLEMENTATION",
                           f"Class `{cls.group(1)}` uses inheritance/interfaces."))
    lf = _language_finding(lang, code, r"^\s*(public|private|protected|internal)?\s*(static\s+)?(fun|[\w<>\[\]]+)\s+\w+\s*\([^)]*\)\s*(:\s*[\w<>?]+\s*)?(throws\s+[\w, ]+)?\s*\{", lang)
    if lf:
        out.append(lf)
    return [f.clamp(text.count("\n") + 1) for f in out[:14]]


# ------------------------------------------------------------------ Go


def scan_go(path: str, text: str, kind: str) -> list[Finding]:
    code = strip_c_comments(text)
    out: list[Finding] = []
    if kind == "test":
        for m in re.finditer(r"func\s+(Test\w+)\s*\(\s*\w+\s+\*testing\.T\s*\)", code):
            s, e, body = _block_lines(code, m.end(), "{", "}", cap=40)
            checks = len(re.findall(r"\bt\.(Error|Errorf|Fatal|Fatalf|Fail)\b|\b(assert|require)\.\w+\(", body))
            out.append(Finding("automated_testing", "go_test_func", line_of(code, m.start()), e, "test_function", "tests_found",
                               "trivial" if checks == 0 else depth_from_size(checks, 1, 2, 4), "CODE_TEST",
                               f"`{m.group(1)}` with {checks} failure check(s)/assertion(s).", candidate_status="rejected" if checks == 0 else "accepted"))
        return [f.clamp(text.count("\n") + 1) for f in out[:8]]
    for m in re.finditer(r"\b(http\.HandleFunc|\w+\.(GET|POST|PUT|DELETE|PATCH|Handle|HandleFunc|Get|Post|Put|Delete|Patch))\s*\(\s*\"(/[^\"]*)\"", code):
        s, e, body = _block_lines(code, m.start(), cap=40)
        n = _stmt_count(body)
        out.append(Finding("api_development", "go_route", s, e, "route_registration", "meaningful_implementation" if n > 3 else "code_usage",
                           "basic" if n <= 3 else "intermediate", "CODE_IMPLEMENTATION", f"Route {m.group(3)} registered via `{m.group(1)}`."))
    for m in re.finditer(r"\.(Query|QueryRow|Exec|QueryContext|QueryRowContext|ExecContext|Prepare|Select|Get)\s*\(\s*(ctx\s*,\s*)?(&?\w+\s*,\s*)?[\"`]\s*" + SQL_KW, code, re.I):
        s, e, body = _block_lines(code, m.start())
        out.append(Finding("sql", "go_sql_exec", s, max(e, s + 3), "sql_execute_call", "meaningful_implementation", "intermediate", "CODE_IMPLEMENTATION",
                           f"Executes SQL via `.{m.group(1)}`."))
    gorm = re.search(r"\bdb\.(Create|Find|First|Where|Save|Delete|Model|AutoMigrate)\s*\(", code)
    if gorm and "gorm" in code:
        s = line_of(code, gorm.start())
        out.append(Finding("sql", "gorm_query", s, s + 8, "orm_query", "meaningful_implementation", "intermediate", "CODE_IMPLEMENTATION", "GORM database operation."))
    conc = list(re.finditer(r"\bgo\s+(func\b|\w+\()|make\(\s*chan\b|sync\.(WaitGroup|Mutex|RWMutex)|\bselect\s*\{", code))
    if conc:
        s, e, _ = _block_lines(code, conc[0].start(), "{", "}", cap=30)
        out.append(Finding("concurrency", "go_concurrency", s, max(e, s + 2), "concurrency_primitive", "meaningful_implementation",
                           depth_from_size(len(conc), 1, 3, 6), "CODE_IMPLEMENTATION", f"Goroutines/channels/sync primitives ({len(conc)} occurrence(s))."))
    auth = re.search(r"\bjwt\.(Parse|NewWithClaims)|bcrypt\.(GenerateFromPassword|CompareHashAndPassword)", code)
    if auth:
        s = line_of(code, auth.start())
        out.append(Finding("auth_security", "go_auth", s, s + 10, "auth_call", "code_usage", "basic", "CODE_IMPLEMENTATION", f"`{auth.group(0)}` used."))
    for m in re.finditer(r"^\s*\"([^\"]+)\"\s*$|^import\s+\"([^\"]+)\"", code, re.M):
        mod = m.group(1) or m.group(2)
        for lib in LIBS:
            if any(mod.startswith(g) for g in lib.go):
                s = line_of(code, m.start())
                out.append(Finding(lib.skill_key, f"go_import:{lib.key}", s, s + 3, "library_import", "code_usage", "basic", "CODE_IMPLEMENTATION", f"{lib.label} imported ({mod})."))
    lf = _language_finding("go", code, r"^func\s+(\([^)]*\)\s*)?\w+\s*\(", "go")
    if lf:
        out.append(lf)
    return [f.clamp(text.count("\n") + 1) for f in out[:14]]


# ------------------------------------------------------------------ others (rule tables)

OTHER_RULES: dict[str, list[tuple[str, str, str, str, str, str]]] = {
    "csharp": [
        ("api_development", r"\[Http(Get|Post|Put|Delete|Patch)(\([^)]*\))?\]|\bapp\.Map(Get|Post|Put|Delete)\s*\(", "aspnet_route", "route", "code_usage", "ASP.NET route"),
        ("sql", r"\bDbContext\b|\bDbSet<|\.FromSqlRaw\(|SqlCommand\(", "ef_core", "orm", "code_usage", "Entity Framework / ADO.NET data access"),
        ("automated_testing", r"\[(Fact|Test|TestMethod|Theory)\][\s\S]{0,300}?Assert\.\w+\(", "dotnet_test", "test", "tests_found", "xUnit/NUnit/MSTest test with Assert"),
    ],
    "rust": [
        ("api_development", r"#\[(get|post|put|delete)\(\"/|\.route\(\s*\"/", "rust_route", "route", "code_usage", "actix/axum/rocket route"),
        ("sql", r"sqlx::query|diesel::|\.execute\(\s*&?\w*\s*,?\s*\"\s*(SELECT|INSERT|UPDATE|DELETE)", "rust_sql", "sql", "meaningful_implementation", "SQL via sqlx/diesel"),
        ("automated_testing", r"#\[(tokio::)?test\][\s\S]{0,400}?assert(_eq|_ne)?!\(", "rust_test", "test", "tests_found", "Rust #[test] with assert!"),
        ("concurrency", r"tokio::spawn|std::thread::spawn|Arc<Mutex<|mpsc::channel", "rust_concurrency", "concurrency", "code_usage", "Rust concurrency primitive"),
    ],
    "php": [
        ("api_development", r"Route::(get|post|put|delete|patch|apiResource|resource)\s*\(", "laravel_route", "route", "code_usage", "Laravel route"),
        ("sql", r"DB::(select|insert|update|delete|table)\(|->prepare\(\s*['\"]\s*(SELECT|INSERT|UPDATE|DELETE)|extends\s+Model\b", "php_sql", "sql", "code_usage", "PHP database access"),
        ("automated_testing", r"function\s+test\w+\s*\([\s\S]{0,400}?\$this->assert\w+\(", "phpunit_test", "test", "tests_found", "PHPUnit test with assertion"),
        ("laravel", r"Illuminate\\", "laravel_usage", "library", "code_usage", "Laravel framework usage"),
    ],
}
RUBY_RULES = [
    ("api_development", r"^\s*(get|post|put|patch|delete|resources?)\s+['\":]", "rails_route", "route", "code_usage", "Rails/Sinatra route"),
    ("sql", r"<\s*(ApplicationRecord|ActiveRecord::Base)|\.(where|find_by|create!?|joins)\(|create_table\s+:", "active_record", "orm", "code_usage", "ActiveRecord model/query/migration"),
    ("automated_testing", r"\bit\s+['\"][^'\"]+['\"]\s+do[\s\S]{0,400}?expect\(|def\s+test_\w+[\s\S]{0,300}?assert", "rspec_test", "test", "tests_found", "RSpec/Minitest example with expectation"),
    ("rails", r"<\s*(ApplicationController|ApplicationRecord|ActionController::Base)", "rails_usage", "library", "code_usage", "Rails framework usage"),
]


def scan_other(path: str, text: str, kind: str, lang: str) -> list[Finding]:
    code = strip_hash_comments(text) if lang == "ruby" else strip_c_comments(text, hash_comments=(lang == "php"))
    rules = RUBY_RULES if lang == "ruby" else OTHER_RULES.get(lang, [])
    out: list[Finding] = []
    for skill, pat, det, node, sig, why in rules:
        ms = list(re.finditer(pat, code, re.M))
        if not ms:
            continue
        s = line_of(code, ms[0].start())
        e = line_of(code, ms[0].end()) + 8
        out.append(Finding(skill, det, s, e, node, sig, depth_from_size(len(ms), 1, 3, 8), "CODE_TEST" if skill == "automated_testing" else "CODE_IMPLEMENTATION",
                           f"{why} ({len(ms)} occurrence(s)). Lexical detector — shallower than AST analysis."))
    def_pats = {"csharp": r"^\s*(public|private|protected|internal)\s+[\w<>\[\], ]+\s+\w+\s*\(", "rust": r"^\s*(pub\s+)?(async\s+)?fn\s+\w+",
                "php": r"function\s+\w+\s*\(", "ruby": r"^\s*def\s+\w+", "swift": r"^\s*(public\s+|private\s+)?func\s+\w+",
                "scala": r"^\s*def\s+\w+", "cpp": r"^[\w:<>\*&\s]+\s+\**\w+(::\w+)?\s*\([^;]*\)\s*(const)?\s*\{", "c": r"^[\w\*\s]+\s+\**\w+\s*\([^;]*\)\s*\{"}
    if lang in def_pats and kind != "test":
        defs = list(re.finditer(def_pats[lang], code, re.M))
        lines = sum(1 for ln in code.split("\n") if ln.strip())
        if defs and lines >= 10:
            s = line_of(code, defs[0].start())
            depth = "substantial" if lines >= 150 and len(defs) >= 5 else "intermediate" if lines >= 50 and len(defs) >= 2 else "basic"
            out.append(Finding(lang, f"{lang}_language_usage", s, s + 30, "source_file", "code_usage", depth, "CODE_IMPLEMENTATION",
                               f"{lang} source with {len(defs)} definition(s) and {lines} code lines.", meta={"code_lines": lines, "defs": len(defs)}))
    return [f.clamp(text.count("\n") + 1) for f in out]
