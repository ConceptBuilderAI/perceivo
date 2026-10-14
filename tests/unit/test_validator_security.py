"""T5: Security validator (run_security_checks)."""

from __future__ import annotations

import ast

import pytest

from perceivo.validator._security import run_security_checks


def _checks_for(src: str):
    return run_security_checks(ast.parse(src))


def test_blocked_root_import_subprocess():
    checks = _checks_for("import subprocess\n")
    assert any(c.id.startswith("SEC-IMPORT") and c.severity == "error" for c in checks)


def test_blocked_app_import_root():
    checks = _checks_for("import app.routes\n")
    assert any(c.id.startswith("SEC-IMPORT") and c.severity == "error" for c in checks)


def test_blocked_perceivo_internal_import():
    checks = _checks_for("from perceivo._internal.context import _ExecutionContext\n")
    assert any(c.id.startswith("SEC-IMPORT") and c.severity == "error" for c in checks)


def test_blocked_eval_call():
    checks = _checks_for("x = eval('1+1')\n")
    assert any(c.id.startswith("SEC-CALL") and "eval" in c.message for c in checks)


def test_blocked_subprocess_run_call():
    src = "import subprocess\nsubprocess.run(['ls'])\n"
    checks = _checks_for(src)
    assert any(c.id.startswith("SEC-PROCESS") for c in checks)


def test_redis_direct_import_warning_only():
    checks = _checks_for("import redis\n")
    redis = [c for c in checks if c.id.startswith("SEC-REDIS")]
    assert redis
    assert all(c.severity == "warning" for c in redis)


def test_external_url_warning():
    checks = _checks_for('URL = "https://example.com/path"\n')
    urls = [c for c in checks if c.id.startswith("SEC-URL")]
    assert urls
    assert all(c.severity == "warning" for c in urls)


def test_safe_code_produces_no_findings():
    src = "def add(a, b):\n    return a + b\n"
    assert _checks_for(src) == []


@pytest.mark.parametrize("name", ["exec", "compile", "__import__"])
def test_other_blocked_builtins(name):
    src = f"x = {name}('1')\n" if name != "__import__" else "x = __import__('os')\n"
    checks = _checks_for(src)
    assert any(c.id.startswith("SEC-CALL") for c in checks)
