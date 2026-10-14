"""T4: create() scaffolding and orchestration templates from the catalog."""

from __future__ import annotations

import ast
import inspect

import pytest
from click.testing import CliRunner

import perceivo
from perceivo._cli import main as perceivo_cli
from perceivo.orchestration._builder._templates import create


def _extract_ai_invoke_call(source: str) -> ast.Call:
    """Return the AST node for the scaffolded ``managed_service.ai.invoke(...)`` call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "invoke"
        ):
            return node
    raise AssertionError("no *.invoke(...) call found in the scaffolded AGENT component")


def _from_template(name: str, workspace, template: str):
    return perceivo.templates.use(template, into=str(workspace), as_name=name)


def test_orchestration_templates_are_in_the_catalog():
    items = perceivo.templates.list(kind="orchestration")
    names = {t.name for t in items}
    assert {"rag-ingest", "rag-query", "chatbot-simple"} <= names
    for t in items:
        assert t.description


def test_create_default_scaffolds_start_agent_end(tmp_workspace):
    path = create("my_default", str(tmp_workspace))
    assert path.exists()
    assert (path / "orchestration.yaml").exists()
    assert (path / "perceivo.yaml").exists()
    assert (path / ".env").exists()
    # 3-node minimal: START + AGENT + END produces 3 component scripts.
    for fn in ("start.py", "agent.py", "end.py"):
        assert (path / fn).exists()


def test_create_with_template_writes_chain_components(tmp_workspace):
    path = _from_template("rag_proj", tmp_workspace, "rag-ingest")
    assert (path / "orchestration.yaml").exists()
    for fn in (
        "start.py",
        "pdf_reader.py",
        "text_splitter.py",
        "embeddings.py",
        "milvus_upsert.py",
        "end.py",
    ):
        assert (path / fn).exists(), f"missing {fn}"


def test_create_unknown_template_raises(tmp_workspace):
    with pytest.raises(perceivo.types.NotFoundError, match="Unknown template"):
        _from_template("x", tmp_workspace, "not-a-real-template")


def test_create_takes_no_template_argument():
    """Templates are one catalog: ``perceivo.templates.use``, not a create flag."""
    assert "template" not in inspect.signature(create).parameters


def test_create_propagates_description(tmp_workspace):
    path = create("dx", str(tmp_workspace), description="my custom desc")
    yaml_text = (path / "orchestration.yaml").read_text(encoding="utf-8")
    assert "my custom desc" in yaml_text


def test_create_with_template_default_description_used_when_none(tmp_workspace):
    path = _from_template("chat", tmp_workspace, "chatbot-simple")
    yaml_text = (path / "orchestration.yaml").read_text(encoding="utf-8")
    # The template's own description should land in orchestration.yaml.
    assert "chatbot" in yaml_text.lower() or "chat" in yaml_text.lower()


# --- the scaffolded AGENT's ai.invoke call is callable ---
#
# perceivo/managed_service/ai.py declares
# ``invoke(service, *, model=None, **params)`` with ``service`` required. A
# scaffold that emits ``invoke(model=model, ...)`` with no ``service`` makes
# every scaffolded AGENT raise TypeError at first call, and
# ``validate_component`` (a static/manifest check) reports it valid anyway --
# it never calls the emitted code, so it is not a sufficient instrument here.


def test_agent_template_invoke_call_binds_against_the_real_signature(tmp_workspace):
    """The emitted call's shape must satisfy managed_service.ai.invoke's signature.

    Placeholder objects stand in for the real arguments -- this checks the
    *call shape* (positional/keyword arity), not runtime values, which is
    exactly what a missing ``service`` argument breaks. ``chatbot-simple``'s
    ``chat`` node is a ``prebuilt`` AGENT, which is the code path
    ``_write_prebuilt``'s AGENT branch actually writes (the bare
    ``create()`` default goes through ``perceivo.component.create``, a
    separate decorator-based scaffold with no ai.invoke call at all).
    """
    path = _from_template("bind_check", tmp_workspace, "chatbot-simple")
    source = (path / "chat.py").read_text(encoding="utf-8")
    call = _extract_ai_invoke_call(source)

    args = [object() for _ in call.args]
    kwargs = {kw.arg: object() for kw in call.keywords if kw.arg is not None}

    # Fails with "missing a required argument: 'service'" against the
    # unmodified scaffold, which emits no positional argument at all.
    inspect.signature(perceivo.managed_service.ai.invoke).bind(*args, **kwargs)


def test_agent_template_pins_the_default_model_deliberately(tmp_workspace):
    """The default model string is a pinned platform value, not the template's to change."""
    path = _from_template("pin_check", tmp_workspace, "chatbot-simple")
    source = (path / "chat.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    default_literal = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "model"
        ):
            default_literal = node.args[1]
            break
    assert default_literal is not None, "perceivo.params.get('model', ...) not found"
    assert isinstance(default_literal, ast.Constant)
    assert default_literal.value == "gpt-4o"


def test_agent_template_runs_against_the_local_chat_mock(tmp_workspace):
    """End to end: the scaffolded AGENT executes without TypeError.

    Against a scaffold whose call omits ``service`` this fails with exit_code 1 and
    ``TypeError: invoke() missing 1 required positional argument: 'service'``
    surfacing through the component runner.
    """
    path = _from_template("run_check", tmp_workspace, "chatbot-simple")
    result = CliRunner().invoke(
        perceivo_cli,
        [
            "run",
            "component",
            str(path / "chat.py"),
            "--type",
            "AGENT",
            "--input",
            '{"prompt": "hello"}',
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "RESULT: PASS" in result.output
