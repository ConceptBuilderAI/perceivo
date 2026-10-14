"""The platform's read-only statement refusals, applied to local runs.

Two rules apply, one per kind of database, as on the platform:

* A read-only query on a platform database (:func:`refuse_write_in_read_only`) is
  refused when its first statement keyword, read past an ``EXPLAIN``/``DESCRIBE``
  prefix and its option words, is one of :data:`BLOCKED_STATEMENTS`.
* A read-only query on an external database
  (:func:`refuse_write_in_external_read_only`) is refused when it holds more than
  one statement, then by the same leading-keyword rule, then unless it begins
  with one of :data:`ALLOWED_LEADING` and contains none of
  :data:`FORBIDDEN_ANYWHERE`, so a data-modifying CTE and ``SELECT ... INTO``
  are refused too.

Comments are blanked and literal bodies masked before any keyword is read, so
neither can hide a keyword nor supply one. Each refusal is the platform's: HTTP
400 with the platform's ``detail``.

The platform also refuses server-side file and network functions on both paths,
and runs an external read-only query in a read-only transaction. A local run
reproduces neither.
"""

from __future__ import annotations

import re

import httpx

BLOCKED_STATEMENTS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "COPY",
        "VACUUM",
        "MERGE",
        "CALL",
        "DO",
        "REPLACE",
        "EXECUTE",
        "PREPARE",
        "LOAD",
        "IMPORT",
        "REINDEX",
        "REFRESH",
        "CLUSTER",
        "COMMENT",
        "SECURITY",
        "HANDLER",
    }
)

ALLOWED_LEADING = ("SELECT", "WITH", "VALUES", "TABLE")

FORBIDDEN_ANYWHERE = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "DROP",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "COPY",
        "VACUUM",
        "CALL",
        "INTO",
        "OUTFILE",
        "DUMPFILE",
        "EXECUTE",
        "PREPARE",
        "REINDEX",
    }
)

_EXPLAIN_OPTION_WORDS = frozenset(
    {
        "ANALYZE",
        "ANALYSE",
        "VERBOSE",
        "COSTS",
        "SETTINGS",
        "GENERIC_PLAN",
        "BUFFERS",
        "WAL",
        "TIMING",
        "SUMMARY",
        "MEMORY",
        "SERIALIZE",
        "FORMAT",
        "TEXT",
        "XML",
        "JSON",
        "YAML",
        "ON",
        "OFF",
        "TRUE",
        "FALSE",
    }
)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")


def _block_comment_end(sql: str, start: int) -> int:
    # Block comments nest, so ``/* /* */ */`` is one comment.
    depth = 0
    i = start
    while i < len(sql):
        if sql.startswith("/*", i):
            depth += 1
            i += 2
        elif sql.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return len(sql)


def _quoted_end(sql: str, start: int, quote: str) -> int:
    # A doubled quote continues the literal; a backslash is not an escape.
    i = start + 1
    while i < len(sql):
        if sql[i] == quote:
            if i + 1 < len(sql) and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return len(sql)


def _skeleton(sql: str) -> str:
    """Return *sql* with comments and literal bodies blanked, at the same length.

    The length is kept so a ``;`` found in the skeleton is an offset into *sql*.
    """
    out: list[str] = []
    i = 0
    while i < len(sql):
        char = sql[i]
        end = None
        kept = ""
        if char in "'\"`":
            end = _quoted_end(sql, i, char)
        elif char == "$" and (tag := _DOLLAR_TAG.match(sql, i)):
            close = sql.find(tag.group(0), tag.end())
            end = len(sql) if close == -1 else close + len(tag.group(0))
        elif sql.startswith("--", i) or char == "#":
            newline = sql.find("\n", i)
            end = len(sql) if newline == -1 else newline
        elif sql.startswith("/*", i):
            end = _block_comment_end(sql, i)
            # ``/*! ... */`` is executed by MySQL, so its body stays visible.
            if sql.startswith("/*!", i):
                body_end = end - 2 if sql.endswith("*/", 0, end) else end
                kept = sql[i + 3 : body_end]
        if end is None:
            out.append(char)
            i += 1
        else:
            out.append(f"   {kept}".ljust(end - i) if kept else " " * (end - i))
            i = end
    return "".join(out)


def _leading_keyword(tokens: list[str]) -> str:
    # ``EXPLAIN ANALYZE INSERT`` runs the INSERT, so the keyword after the options counts.
    if not tokens:
        return ""
    if tokens[0] not in ("EXPLAIN", "DESCRIBE", "DESC"):
        return tokens[0]
    index = 1
    while index < len(tokens) and tokens[index] in _EXPLAIN_OPTION_WORDS:
        index += 1
    return tokens[index] if index < len(tokens) else tokens[0]


def _statements(sql: str) -> list[str]:
    # A ``;`` inside a literal or a comment is not a boundary.
    skeleton = _skeleton(sql)
    pieces: list[str] = []
    start = 0
    for index, char in enumerate(skeleton):
        if char == ";":
            pieces.append(sql[start:index])
            start = index + 1
    pieces.append(sql[start:])
    return [piece for piece in pieces if piece.strip()]


def _refuse(path: str, detail: str) -> None:
    request = httpx.Request("POST", f"http://local/{path}/query")
    httpx.Response(400, json={"detail": detail}, request=request).raise_for_status()


def _blocked_word(sql: str) -> str:
    word = _leading_keyword(_WORD.findall(_skeleton(sql.upper())))
    return word if word in BLOCKED_STATEMENTS else ""


def refuse_write_in_read_only(database_id: str, sql: str) -> None:
    """Raise the platform's refusal if *sql* leads with a blocked statement.

    Raises:
        httpx.HTTPStatusError: Status 400, body ``{"detail": "Statement '<WORD>'
            not allowed in read-only mode"}``.
    """
    if word := _blocked_word(sql or ""):
        _refuse(
            f"databases/internal/{database_id}",
            f"Statement '{word}' not allowed in read-only mode",
        )


def refuse_write_in_external_read_only(connection_id: str, sql: str) -> None:
    """Raise the platform's refusal if *sql* is not a single read statement.

    The checks run in the platform's order, and the first that fails refuses.

    Raises:
        httpx.HTTPStatusError: Status 400, with the ``detail`` of the check that
            refused: more than one statement, a blocked leading statement, a
            leading keyword outside :data:`ALLOWED_LEADING`, or a keyword from
            :data:`FORBIDDEN_ANYWHERE` anywhere in the statement.
    """
    sql = sql or ""
    path = f"databases/external/{connection_id}"
    if len(_statements(sql)) > 1:
        _refuse(path, "Only a single SQL statement may be submitted per request")
    if word := _blocked_word(sql):
        _refuse(path, f"Statement '{word}' not allowed in read-only mode")
    tokens = _WORD.findall(_skeleton(sql).upper())
    if not tokens:
        _refuse(path, "sql is required")
    if tokens[0] not in ALLOWED_LEADING:
        _refuse(
            path,
            f"Statement '{tokens[0]}' is not a read statement. Read-only queries "
            f"must begin with one of: {', '.join(ALLOWED_LEADING)}.",
        )
    forbidden = [token for token in tokens if token in FORBIDDEN_ANYWHERE]
    if forbidden:
        _refuse(
            path,
            f"Keyword '{forbidden[0]}' is not allowed in a read-only query "
            "(including inside a common table expression).",
        )
