"""Security validation checks for component scripts."""

from __future__ import annotations

import ast
import re
from typing import Any, Callable

from ._report import ValidationCheck

_AddFn = Callable[..., None]


BLOCKED_IMPORT_ROOTS: set[str] = {
    "app",
    "subprocess",
    "multiprocessing",
    "socket",
    "socketserver",
    "ctypes",
    "cffi",
}

BLOCKED_MODULES: set[str] = {
    "subprocess",
    "multiprocessing",
    "socket",
    "ctypes",
    "importlib.util",
    "importlib.machinery",
}

BLOCKED_CALL_NAMES: set[str] = {"eval", "exec", "compile", "__import__"}

BLOCKED_PROCESS_CALLS: set[str] = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_output",
    "subprocess.check_call",
    "os.system",
    "os.popen",
    "os.exec",
    "os.execv",
    "os.execve",
    "os.spawnl",
    "os.spawnle",
    "socket.socket",
}

URL_PATTERN = re.compile(r"https?://[^\s'\"`]+", re.IGNORECASE)


def run_security_checks(tree: ast.AST) -> list[ValidationCheck]:
    """Run all security checks on a parsed AST.

    Returns a list of ValidationCheck findings (errors and warnings).
    """
    checks: list[ValidationCheck] = []
    _counters: dict[str, Any] = {}

    def _add(
        prefix: str,
        severity: str,
        category: str,
        title: str,
        message: str,
        line: int | None = None,
        suggestion: str | None = None,
    ) -> None:
        seq = _counters.get(prefix, 0) + 1
        _counters[prefix] = seq
        checks.append(
            ValidationCheck(
                id=f"{prefix}-{seq:03d}",
                severity=severity,
                category=category,
                title=title,
                message=message,
                line=line,
                suggestion=suggestion,
            )
        )

    for node in ast.walk(tree):
        _check_import(node, _add)
        _check_import_from(node, _add)
        _check_call(node, _add)
        _check_url(node, _add)

    return checks


def _check_import(node: ast.AST, add: _AddFn) -> None:
    """Report blocked imports for a plain ``import x`` statement."""
    if not isinstance(node, ast.Import):
        return
    for alias in node.names:
        root = alias.name.split(".")[0]
        if alias.name.startswith("perceivo._internal") or root in BLOCKED_IMPORT_ROOTS:
            add(
                "SEC-IMPORT",
                "error",
                "security",
                "Blocked import",
                f"Blocked import: {alias.name}",
                line=getattr(node, "lineno", None),
                suggestion="Use public perceivo APIs only.",
            )
        if _is_blocked_module(alias.name):
            add(
                "SEC-IMPORT",
                "error",
                "security",
                "Blocked module",
                f"Blocked module: {alias.name}",
                line=getattr(node, "lineno", None),
            )
        if root in ("redis", "aioredis"):
            add(
                "SEC-REDIS",
                "warning",
                "security",
                "Redis dependency detected",
                "This component imports redis directly. Consider using "
                "perceivo.cluster.redis.connect() instead.",
                line=getattr(node, "lineno", None),
            )


def _check_import_from(node: ast.AST, add: _AddFn) -> None:
    """Report blocked imports for a ``from x import y`` statement."""
    if not isinstance(node, ast.ImportFrom) or not node.module:
        return
    root = node.module.split(".")[0]
    if node.module.startswith("perceivo._internal") or root in BLOCKED_IMPORT_ROOTS:
        add(
            "SEC-IMPORT",
            "error",
            "security",
            "Blocked import",
            f"Blocked import: from {node.module}",
            line=getattr(node, "lineno", None),
            suggestion="Use public perceivo APIs only.",
        )
    if _is_blocked_module(node.module):
        add(
            "SEC-IMPORT",
            "error",
            "security",
            "Blocked module",
            f"Blocked module: {node.module}",
            line=getattr(node, "lineno", None),
        )


def _check_call(node: ast.AST, add: _AddFn) -> None:
    """Report blocked built-in and process calls."""
    if not isinstance(node, ast.Call):
        return
    call_name = _get_call_name(node)
    if call_name in BLOCKED_CALL_NAMES:
        add(
            "SEC-CALL",
            "error",
            "security",
            "Blocked built-in call",
            f"Blocked call: {call_name}()",
            line=getattr(node, "lineno", None),
        )
    if call_name in BLOCKED_PROCESS_CALLS:
        add(
            "SEC-PROCESS",
            "error",
            "security",
            "Blocked process call",
            f"Blocked process call: {call_name}()",
            line=getattr(node, "lineno", None),
        )


def _check_url(node: ast.AST, add: _AddFn) -> None:
    """Report external URLs embedded in string constants."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return
    for url in URL_PATTERN.findall(node.value):
        add(
            "SEC-URL",
            "warning",
            "security",
            "External URL detected",
            f"External URL in string: {url}",
            line=getattr(node, "lineno", None),
        )


def _is_blocked_module(module_name: str) -> bool:
    """Check if a module name is blocked (exact or root match)."""
    if module_name in BLOCKED_MODULES:
        return True
    root = module_name.split(".")[0]
    return root in BLOCKED_MODULES


def _get_call_name(node: ast.Call) -> str:
    """Extract the call name from an ast.Call node."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts = []
        current: ast.expr = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""
