"""Comment stripping for C-like languages (JS/TS/Java/Go/C#/Kotlin/Rust/Swift/PHP).

Comments are replaced by spaces (newlines preserved) so line numbers stay exact while
code inside comments can never become evidence. String literals are kept intact.
Regex literals in JS are not fully modelled; this is documented as a limitation.
"""
from __future__ import annotations


def strip_c_comments(src: str, hash_comments: bool = False) -> str:
    out: list[str] = []
    i = 0
    n = len(src)
    state = "code"
    quote = ""
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line"
                out.append("  ")
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block"
                out.append("  ")
                i += 2
                continue
            if hash_comments and ch == "#":
                state = "line"
                out.append(" ")
                i += 1
                continue
            if ch in ("'", '"', "`"):
                state = "str"
                quote = ch
            out.append(ch)
            i += 1
        elif state == "line":
            if ch == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
        else:  # string
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                state = "code"
            elif ch == "\n" and quote != "`":
                state = "code"  # unterminated single-line string; recover
            i += 1
    return "".join(out)


def strip_hash_comments(src: str) -> str:
    """For YAML/shell/Dockerfile/Procfile/TOML/Ruby: drop text after an unquoted '#'."""
    lines = []
    for line in src.split("\n"):
        res = []
        q = ""
        for idx, ch in enumerate(line):
            if q:
                res.append(ch)
                if ch == q:
                    q = ""
                continue
            if ch in ("'", '"'):
                q = ch
                res.append(ch)
                continue
            if ch == "#" and (idx == 0 or line[idx - 1] in " \t"):
                break
            res.append(ch)
        lines.append("".join(res))
    return "\n".join(lines)


def strip_sql_comments(src: str) -> str:
    out = []
    i, n = 0, len(src)
    state = "code"
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "-" and nxt == "-":
                state = "line"
                i += 2
                out.append("  ")
                continue
            if ch == "/" and nxt == "*":
                state = "block"
                i += 2
                out.append("  ")
                continue
            if ch == "'":
                state = "str"
            out.append(ch)
        elif state == "line":
            if ch == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                out.append("  ")
                continue
            out.append("\n" if ch == "\n" else " ")
        else:
            out.append(ch)
            if ch == "'":
                state = "code"
        i += 1
    return "".join(out)


def match_block(code: str, open_idx: int, open_ch: str = "(", close_ch: str = ")") -> int:
    """Return the index of the bracket closing the one at open_idx (strings respected), or -1."""
    depth = 0
    i = open_idx
    n = len(code)
    q = ""
    while i < n:
        ch = code[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = ""
        elif ch in ("'", '"', "`"):
            q = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def line_of(code: str, idx: int) -> int:
    return code.count("\n", 0, idx) + 1
