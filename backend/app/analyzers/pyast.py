"""Python source model built with the standard-library `ast` module.

Comments never appear in the AST and docstrings are tracked explicitly, so SQL or
routes written only in comments/docstrings can never become evidence.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

SQL_RE = re.compile(
    r"^\s*(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+(TABLE|INDEX|VIEW|UNIQUE)|"
    r"ALTER\s+TABLE|DROP\s+TABLE|WITH\s+\w+\s+AS\s*\(|REPLACE\s+INTO|UPSERT)",
    re.IGNORECASE | re.S,
)


def dotted(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func) + "()"
    if isinstance(node, ast.Subscript):
        return dotted(node.value)
    return ""


@dataclass
class PyCall:
    name: str  # dotted, e.g. "cursor.execute"
    attr: str  # last segment
    line: int
    end_line: int
    first_str: str | None
    arg_names: list[str]
    n_args: int
    kw_names: list[str]
    func: str | None  # enclosing function qualname
    node: ast.Call


@dataclass
class PyFunc:
    name: str
    qualname: str
    line: int
    end_line: int
    decorators: list[tuple[str, str | None, int]]  # (dotted name, first str arg, line)
    is_async: bool
    trivial: bool
    n_statements: int
    class_name: str | None
    node: ast.AST
    raises: list[str] = field(default_factory=list)
    params_annotated: list[str] = field(default_factory=list)


@dataclass
class PyClass:
    name: str
    line: int
    end_line: int
    bases: list[str]
    keywords: dict[str, str]
    methods: list[str]
    field_calls: list[tuple[str, int]]  # e.g. ("Column", line)
    decorators: list[str]


@dataclass
class PyModule:
    ok: bool
    error: str | None
    imports: dict[str, str]  # local alias -> fully qualified module/object
    import_lines: dict[str, int]
    top_modules: set[str]
    funcs: list[PyFunc]
    classes: list[PyClass]
    calls: list[PyCall]
    names_used: dict[str, int]  # identifier -> usage count (Name ids + attribute names), excluding import statements
    sql_strings: list[tuple[int, int, str, str | None]]  # (line, end_line, text, enclosing func)
    sql_vars: dict[tuple[str | None, str], int]  # (func, varname) -> line of SQL assignment
    docstring_lines: set[int]
    lines: list[str]


def _is_trivial_body(body: list[ast.stmt]) -> bool:
    stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    if not stmts:
        return True
    for s in stmts:
        if isinstance(s, ast.Pass):
            continue
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis:
            continue
        if isinstance(s, ast.Raise) and s.exc is not None and "NotImplementedError" in dotted(s.exc.func if isinstance(s.exc, ast.Call) else s.exc):
            continue
        if isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, ast.Constant)):
            continue
        return False
    return True


def _count_statements(node: ast.AST) -> int:
    return sum(1 for n in ast.walk(node) if isinstance(n, ast.stmt)) - 1


def parse_python(src: str) -> PyModule:
    lines = src.split("\n")
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        return PyModule(False, f"{type(e).__name__}: {e}", {}, {}, set(), [], [], [], {}, [], {}, set(), lines)

    imports: dict[str, str] = {}
    import_lines: dict[str, int] = {}
    top: set[str] = set()
    funcs: list[PyFunc] = []
    classes: list[PyClass] = []
    calls: list[PyCall] = []
    names: dict[str, int] = {}
    sql_strings: list[tuple[int, int, str, str | None]] = []
    sql_vars: dict[tuple[str | None, str], int] = {}
    doc_lines: set[int] = set()
    import_nodes: set[int] = set()

    # docstrings
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    doc_lines.add(ln)
                import_nodes.add(id(first.value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                imports[local] = a.name if a.asname else a.name.split(".")[0]
                import_lines[local] = node.lineno
                top.add(a.name.split(".")[0])
                imports.setdefault(a.name, a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and mod:
                top.add(mod.split(".")[0])
            for a in node.names:
                local = a.asname or a.name
                imports[local] = f"{mod}.{a.name}" if mod else a.name
                import_lines[local] = node.lineno

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.func_stack: list[str] = []
            self.class_stack: list[str] = []

        def _cur(self) -> str | None:
            return self.func_stack[-1] if self.func_stack else None

        def visit_Import(self, node: ast.Import) -> None:  # names in imports are not usages
            return

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            fields: list[tuple[str, int]] = []
            methods: list[str] = []
            for st in node.body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(st.name)
                val = None
                if isinstance(st, ast.Assign):
                    val = st.value
                elif isinstance(st, ast.AnnAssign):
                    val = st.value
                if isinstance(val, ast.Call):
                    fields.append((dotted(val.func), st.lineno))
            classes.append(PyClass(
                name=node.name, line=node.lineno, end_line=node.end_lineno or node.lineno,
                bases=[dotted(b) for b in node.bases],
                keywords={k.arg or "": (ast.unparse(k.value) if hasattr(ast, "unparse") else "") for k in node.keywords},
                methods=methods, field_calls=fields, decorators=[dotted(d) for d in node.decorator_list],
            ))
            for b in node.bases:
                self.visit(b)
            for d in node.decorator_list:
                self.visit(d)
            self.class_stack.append(node.name)
            for st in node.body:
                self.visit(st)
            self.class_stack.pop()

        def _func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            decos: list[tuple[str, str | None, int]] = []
            for d in node.decorator_list:
                if isinstance(d, ast.Call):
                    first = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str) else None
                    decos.append((dotted(d.func), first, d.lineno))
                else:
                    decos.append((dotted(d), None, d.lineno))
                self.visit(d)
            cls = self.class_stack[-1] if self.class_stack else None
            qual = f"{cls}.{node.name}" if cls else node.name
            raises = [dotted(r.exc.func if isinstance(r.exc, ast.Call) else r.exc) for r in ast.walk(node) if isinstance(r, ast.Raise) and r.exc is not None]
            annotated = [dotted(a.annotation) for a in node.args.args if a.annotation is not None]
            funcs.append(PyFunc(
                name=node.name, qualname=qual, line=node.lineno, end_line=node.end_lineno or node.lineno,
                decorators=decos, is_async=isinstance(node, ast.AsyncFunctionDef), trivial=_is_trivial_body(node.body),
                n_statements=_count_statements(node), class_name=cls, node=node, raises=raises, params_annotated=annotated,
            ))
            for a in node.args.args + node.args.kwonlyargs:
                if a.annotation is not None:
                    self.visit(a.annotation)
            for dflt in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                self.visit(dflt)
            self.func_stack.append(qual)
            for st in node.body:
                self.visit(st)
            self.func_stack.pop()

        visit_FunctionDef = _func
        visit_AsyncFunctionDef = _func

        def visit_Name(self, node: ast.Name) -> None:
            names[node.id] = names.get(node.id, 0) + 1

        def visit_Attribute(self, node: ast.Attribute) -> None:
            names[node.attr] = names.get(node.attr, 0) + 1
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> None:
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and SQL_RE.search(node.value.value):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        sql_vars[(self._cur(), t.id)] = node.lineno
            if isinstance(node.value, ast.JoinedStr):
                txt = "".join(v.value for v in node.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
                if SQL_RE.search(txt):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            sql_vars[(self._cur(), t.id)] = node.lineno
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str) and id(node) not in import_nodes and SQL_RE.search(node.value):
                sql_strings.append((node.lineno, node.end_lineno or node.lineno, node.value, self._cur()))

        def visit_Call(self, node: ast.Call) -> None:
            name = dotted(node.func)
            first = None
            if node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    first = a0.value
                elif isinstance(a0, ast.JoinedStr):
                    first = "".join(v.value for v in a0.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
                elif isinstance(a0, ast.Call) and dotted(a0.func).split(".")[-1] == "text" and a0.args and isinstance(a0.args[0], ast.Constant):
                    first = str(a0.args[0].value)
            calls.append(PyCall(
                name=name, attr=name.split(".")[-1] if name else "", line=node.lineno, end_line=node.end_lineno or node.lineno,
                first_str=first, arg_names=[a.id for a in node.args if isinstance(a, ast.Name)], n_args=len(node.args),
                kw_names=[k.arg for k in node.keywords if k.arg], func=self._cur(), node=node,
            ))
            self.generic_visit(node)

    Visitor().visit(tree)
    return PyModule(True, None, imports, import_lines, top, funcs, classes, calls, names, sql_strings, sql_vars, doc_lines, lines)


def enclosing_func(mod: PyModule, line: int) -> PyFunc | None:
    best: PyFunc | None = None
    for f in mod.funcs:
        if f.line <= line <= f.end_line and (best is None or f.line >= best.line):
            best = f
    return best


def func_by_qualname(mod: PyModule, qual: str | None) -> PyFunc | None:
    if not qual:
        return None
    for f in mod.funcs:
        if f.qualname == qual:
            return f
    return None
