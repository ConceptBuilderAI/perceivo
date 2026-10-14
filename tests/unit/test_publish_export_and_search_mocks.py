"""Behaviour of ``orchestration.publish``, the ``perceivo export`` command and the
in-memory Elasticsearch/Milvus mocks.

Bundle construction is replaced at its import site, and every request goes to an
httpx ``MockTransport`` (the ``api`` fixture), so these tests pin what each unit
does: where the bundle is POSTed, how the platform's id is read back, which
output file the CLI chooses, and what the mocks store and return.
"""

from __future__ import annotations

import json
import sys

import httpx
import pytest
from click.testing import CliRunner

import perceivo.orchestration.publish  # noqa: F401  (the package re-exports the function under this name)
from perceivo._bundle import component as component_bundle
from perceivo._bundle import orchestration as orchestration_bundle
from perceivo._cli.export_cmd import export
from perceivo.local.mocks.search import MockElasticsearch
from perceivo.local.mocks.vector import MockMilvus
from perceivo.types.errors import PerceivoError

publish_mod = sys.modules["perceivo.orchestration.publish"]

# --- publish -----------------------------------------------------------------

_BUNDLE = {"orchestration": {"name": "orig"}}


@pytest.fixture
def pub(api, monkeypatch):
    """The ``api`` capture, with an API key set and a fixed bundle."""
    monkeypatch.setenv("PERCEIVO_API_KEY", "key-1")
    monkeypatch.setattr(
        publish_mod, "orchestration_to_bundle", lambda path: json.loads(json.dumps(_BUNDLE))
    )
    return api


def _reply(capture, status: int, body=None, text: str | None = None) -> None:
    if text is not None:
        capture.api = lambda request: httpx.Response(status, content=text.encode("utf-8"))
    else:
        capture.api = lambda request: httpx.Response(status, json=body)


async def test_publish_posts_the_bundle_to_the_import_path_with_credentials(pub, tmp_path):
    pub.api = lambda request: httpx.Response(201, json={"asset_id": "orch-1"})
    result = await publish_mod.publish(str(tmp_path), name="renamed")
    (sent,) = pub.api_requests
    assert str(sent.url) == "https://api.perceivo.ai/v1/orchestrations/import"
    assert sent.headers["X-API-Key"] == "key-1"
    assert sent.headers["Content-Type"] == "application/json"
    body = {"orchestration": {"name": "renamed"}, "new_name": "renamed"}
    assert json.loads(sent.content) == body
    assert pub.client_kwargs[0]["timeout"] == 60.0
    assert result == publish_mod.PublishResult(
        orchestration_id="orch-1",
        bundle=body,
        status_code=201,
        response={"asset_id": "orch-1"},
        dry_run=False,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"asset_id": None, "orchestration_id": "a", "id": "b"},
        {"orchestration_id": "a", "id": "b"},
        {"id": "b", "orchestration": {"id": "c"}},
        {"orchestration": {"id": "c"}},
        {"asset_id": ""},
        ["not", "a", "dict"],
    ],
)
async def test_publish_reads_asset_id_only(pub, tmp_path, body):
    """The fallbacks to ``orchestration_id``/``id``/``orchestration.id`` are gone: a
    success without ``asset_id`` names nothing that can be found again."""
    _reply(pub, 201, body)
    with pytest.raises(publish_mod.PublishError) as err:
        await publish_mod.publish(str(tmp_path))
    assert "no asset_id" in str(err.value)
    assert err.value.status_code == 201


async def test_publish_refuses_a_non_json_success_body(pub, tmp_path):
    _reply(pub, 204, text="")
    with pytest.raises(publish_mod.PublishError) as err:
        await publish_mod.publish(str(tmp_path))
    assert (err.value.status_code, err.value.body) == (204, None)


#: The platform data service's ``ImportResult`` as the platform API service relays
#: it on a 201 -- the id of the new orchestration is ``asset_id``, and there is no
#: ``id`` key.
_IMPORT_RESULT = {
    "success": True,
    "asset_id": "5b0c9d2e-0000-4000-8000-000000000001",
    "asset_name": "My orchestration",
    "components_created": 2,
    "component_ids": ["c-1", "c-2"],
    "created_orchestration_ids": ["5b0c9d2e-0000-4000-8000-000000000001"],
    "referenced_orchestration_ids": [],
    "unresolved_secrets": [],
    "validation_warnings": [],
}


async def test_publish_reads_the_id_from_the_real_import_result(pub, tmp_path):
    """Returning ``None`` would make the CLI print ``orchestration_id=None``
    for every successful publish."""
    _reply(pub, 201, dict(_IMPORT_RESULT))
    result = await publish_mod.publish(str(tmp_path))
    assert result.orchestration_id == _IMPORT_RESULT["asset_id"]


def _validation_failure(padding: int) -> dict:
    """A component refusal as the create path raises it, with ``padding`` pass
    checks ahead of the failing one -- a real report lists every check it ran,
    so the failing id lands far past any fixed-length cut of the raw body."""
    passes = [
        {
            "id": f"VAL-PASS-{i:03d}",
            "category": "contract",
            "severity": "pass",
            "title": "ok",
            "message": "fine " * 10,
        }
        for i in range(padding)
    ]
    failing = {
        "id": "VAL-CONTRACT-OPTIONAL-001",
        "category": "contract",
        "severity": "error",
        "title": "Optional field read as required",
        "message": "Port 'input' field 'topic' is optional but the script indexes it.",
    }
    return {
        "detail": {
            "error": "COMPONENT_VALIDATION_FAILED",
            "message": "Component failed compatibility validation.",
            "validation_report": {"valid": False, "checks": [*passes, failing]},
        }
    }


async def test_publish_error_names_every_failing_check_past_the_old_512_char_cut(pub, tmp_path):
    body = _validation_failure(padding=20)
    text = json.dumps(body)
    assert text.index("VAL-CONTRACT-OPTIONAL-001") > 512  # the id a 512-byte cut would hide
    _reply(pub, 400, text=text)

    with pytest.raises(publish_mod.PublishError) as err:
        await publish_mod.publish(str(tmp_path))

    assert str(err.value) == (
        "publish: API returned 400: COMPONENT_VALIDATION_FAILED: "
        "Component failed compatibility validation.\n"
        "  [VAL-CONTRACT-OPTIONAL-001] error: Optional field read as required -- "
        "Port 'input' field 'topic' is optional but the script indexes it."
    )
    assert isinstance(err.value, RuntimeError)  # callers catching RuntimeError still work
    assert isinstance(err.value, PerceivoError)  # and so do callers catching the SDK root
    assert err.value.status_code == 400
    assert err.value.body == body
    assert err.value.text == text
    assert [c["id"] for c in err.value.checks] == ["VAL-CONTRACT-OPTIONAL-001"]


@pytest.mark.parametrize(
    ("body", "text", "expected"),
    [
        ({"detail": "bad"}, "ignored", "publish: API returned 400: bad"),
        (
            {"detail": {"error": "IMPORT_FAILED", "message": "boom"}},
            "",
            "publish: API returned 400: IMPORT_FAILED: boom",
        ),
        (
            {"detail": {"error": "INVALID_SCRIPT", "message": "no", "blocked_imports": ["os"]}},
            "",
            'publish: API returned 400: INVALID_SCRIPT: no {"blocked_imports": ["os"]}',
        ),
        (
            {"detail": {"errors": [{"code": "ORCH-PHANTOM-001", "message": "dangling"}, "raw"]}},
            "",
            "publish: API returned 400: request rejected\n  [ORCH-PHANTOM-001] dangling\n  raw",
        ),
        (
            {"detail": [{"loc": ["body", "nodes"], "msg": "field required", "type": "missing"}]},
            "",
            "publish: API returned 422: request rejected\n  body.nodes: field required",
        ),
        ({"detail": {}}, "", "publish: API returned 400: {}"),
        (
            {"detail": {"errors": [{"field": "nodes"}]}},
            "",
            'publish: API returned 400: request rejected\n  {"field": "nodes"}',
        ),
        ({"detail": 7}, "raw-7", "publish: API returned 400: raw-7"),
        ({"message": "no detail key"}, "", "publish: API returned 400: no detail key"),
        (None, "y" * 600, "publish: API returned 400: " + "y" * 600),
        (None, "", "publish: API returned 400: (empty response body)"),
    ],
)
def test_publish_error_renders_each_server_error_shape_in_full(body, text, expected):
    status = 422 if isinstance((body or {}).get("detail"), list) else 400
    assert str(publish_mod.PublishError(status, body, text)) == expected


def test_publish_cli_prints_the_asset_id_the_server_returns(pub, tmp_path):
    """End to end through the real ``publish`` coroutine: the CLI line that read
    ``orchestration_id=None`` on every successful publish."""
    from perceivo._cli import main as perceivo_cli

    _reply(pub, 201, dict(_IMPORT_RESULT))
    result = CliRunner().invoke(perceivo_cli, ["publish", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert f"orchestration_id={_IMPORT_RESULT['asset_id']}" in result.output


def test_publish_cli_reports_the_failing_check_without_a_traceback(pub, tmp_path):
    from perceivo._cli import main as perceivo_cli

    body = _validation_failure(padding=20)
    _reply(pub, 400, text=json.dumps(body))
    result = CliRunner().invoke(perceivo_cli, ["publish", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "[VAL-CONTRACT-OPTIONAL-001] error:" in result.output
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, publish_mod.PublishError)


async def test_publish_dry_run_sends_nothing(pub, tmp_path):
    result = await publish_mod.publish(str(tmp_path), dry_run=True)
    assert pub.requests == []
    assert (result.dry_run, result.status_code, result.orchestration_id) == (True, None, None)


# --- perceivo export ---------------------------------------------------------


@pytest.fixture
def builders(monkeypatch):
    built: list[tuple[str, str]] = []

    def _component(path, *args):
        built.append(("component", path.name))
        return {"component": {"name": f"comp-{path.stem}"}}

    def _orchestration(path):
        built.append(("orchestration", path.name))
        return {"orchestration": {"name": "flow"}} if path.name != "unnamed" else {}

    monkeypatch.setattr(component_bundle, "component_to_bundle", _component)
    monkeypatch.setattr(orchestration_bundle, "orchestration_to_bundle", _orchestration)
    return built


def _run(tmp_path, *args):
    return CliRunner().invoke(export, [str(a) for a in args], catch_exceptions=False)


def test_export_a_component_file_defaults_to_its_name(tmp_path, builders, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.py").write_text("", encoding="utf-8")
    result = _run(tmp_path, tmp_path / "agent.py")
    assert result.exit_code == 0
    written = tmp_path / "comp-agent.bundle.json"
    assert result.output.strip() == str(written.resolve())
    assert json.loads(written.read_text(encoding="utf-8")) == {"component": {"name": "comp-agent"}}
    assert builders == [("component", "agent.py")]


@pytest.mark.parametrize(("project", "stem"), [("proj", "flow"), ("unnamed", "orchestration")])
def test_export_an_orchestration_project_names_the_file_from_the_bundle(
    tmp_path, builders, monkeypatch, project, stem
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / project).mkdir()
    (tmp_path / project / "orchestration.yaml").write_text("", encoding="utf-8")
    (tmp_path / project / "a.py").write_text("", encoding="utf-8")
    result = _run(tmp_path, tmp_path / project)
    assert result.exit_code == 0
    assert (tmp_path / f"{stem}.bundle.json").is_file()
    assert builders == [("orchestration", project)]


def test_export_a_single_component_directory_uses_its_one_script(tmp_path, builders, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "only.py").write_text("", encoding="utf-8")
    out = tmp_path / "out.json"
    result = _run(tmp_path, tmp_path / "c", "-o", out)
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"component": {"name": "comp-only"}}


def test_export_refuses_an_ambiguous_directory(tmp_path, builders):
    (tmp_path / "many").mkdir()
    for n in ("a.py", "b.py"):
        (tmp_path / "many" / n).write_text("", encoding="utf-8")
    result = _run(tmp_path, tmp_path / "many")
    assert result.exit_code == 1
    assert "nor a single-component directory (2 .py files found)" in result.output
    assert builders == []


def test_export_refuses_a_file_that_is_not_python(tmp_path, builders):
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    result = _run(tmp_path, tmp_path / "notes.txt")
    assert result.exit_code == 1
    assert "expected a component .py or a project dir" in result.output


def test_export_refuses_an_output_path_that_climbs(tmp_path, builders):
    (tmp_path / "agent.py").write_text("", encoding="utf-8")
    result = _run(tmp_path, tmp_path / "agent.py", "-o", "../escape.json")
    assert result.exit_code == 1
    assert "must not contain '..'" in result.output
    assert not (tmp_path.parent / "escape.json").exists()


# --- MockElasticsearch -------------------------------------------------------


async def test_es_indices_are_created_listed_and_dropped():
    es = MockElasticsearch({})
    assert await es.create_index("b") == {"index": "b", "acknowledged": True}
    await es.create_index("a")
    assert await es.list_indices() == ["a", "b"]
    assert await es.delete_index("a") == {"index": "a", "acknowledged": True}
    await es.delete_index("never-existed")
    assert await es.list_indices() == ["b"]


async def test_es_search_returns_indexed_documents_ranked_and_capped():
    es = MockElasticsearch({"search_hits": [{"id": "seed"}]})
    assert await es.bulk_index("i", [{"t": 1}, {"t": 2}, {"t": 3}], doc_ids=[10, 11]) == {
        "index": "i",
        "indexed": 3,
    }
    result = await es.search("i", size=2)
    assert result["hits"]["total"] == {"value": 2}
    assert result["hits"]["hits"] == [
        {"_id": "10", "_score": 1.0, "_source": {"t": 1}},
        {"_id": "11", "_score": 0.99, "_source": {"t": 2}},
    ]
    assert (await es.search("i"))["hits"]["hits"][2]["_id"] == "i-2"


async def test_es_search_falls_back_to_seed_hits_for_an_empty_index():
    es = MockElasticsearch(
        {"search_hits": [{"id": "s1", "score": 0.7, "title": "x"}, {"body": "y"}]}
    )
    hits = (await es.search("empty", size=5))["hits"]["hits"]
    assert hits == [
        {"_id": "s1", "_score": 0.7, "_source": {"title": "x"}},
        {"_id": "mock-es", "_score": 1.0, "_source": {"body": "y"}},
    ]
    assert (await MockElasticsearch(None).search("empty"))["hits"]["hits"] == []


async def test_es_deletes_by_id_and_by_query():
    es = MockElasticsearch({})
    await es.bulk_index("i", [{"a": 1}, {"a": 2}], doc_ids=["x", "y"])
    assert await es.delete_document("i", "x") == {"index": "i", "deleted": 1}
    assert await es.delete_document("i", "x") == {"index": "i", "deleted": 0}
    assert await es.delete_by_query("i", {"match_all": {}}) == {"index": "i", "deleted": 1}
    assert (await es.search("i"))["hits"]["hits"] == []


# --- MockMilvus --------------------------------------------------------------


async def test_milvus_collections_are_created_listed_and_dropped():
    mv = MockMilvus({})
    assert await mv.create_collection("b", dimension=3) == {
        "collection_name": "b",
        "dimension": 3,
        "metric_type": "COSINE",
    }
    await mv.create_collection("a")
    assert (await mv.has_collection("a"), await mv.has_collection("z")) == (True, False)
    assert await mv.list_collections() == ["a", "b"]
    assert await mv.drop_collection("a") == {"collection_name": "a", "dropped": True}
    assert await mv.list_collections() == ["b"]


async def test_milvus_search_returns_inserted_entries_per_query_vector():
    mv = MockMilvus({"search_hits": [{"id": "seed"}]})
    inserted = await mv.insert(
        "c", [[1], [2], [3]], metadata=[{"text": "one", "metadata": {"k": 1}}]
    )
    assert inserted == {"insert_count": 3, "collection_name": "c"}
    results = await mv.search("c", [[0], [9]], top_k=2)
    assert len(results) == 2
    assert results[0] == [
        {"id": "c-0", "distance": 0.95, "entity": {"text": "one", "metadata": {"k": 1}}},
        {"id": "c-1", "distance": 0.94, "entity": {"text": "", "metadata": {}}},
    ]
    assert results[1] == results[0]


async def test_milvus_search_falls_back_to_seed_hits_and_one_empty_query():
    mv = MockMilvus(
        {"search_hits": [{"id": "s", "distance": 0.2, "text": "t", "metadata": {"m": 1}}, {}]}
    )
    assert await mv.search("empty", [], top_k=5) == [
        [
            {"id": "s", "distance": 0.2, "entity": {"text": "t", "metadata": {"m": 1}}},
            {"id": "mock-milvus", "distance": 0.95, "entity": {"text": "", "metadata": {}}},
        ]
    ]
    assert await MockMilvus(None).search("empty", [[1]]) == [[]]


async def test_milvus_search_ranks_by_the_collection_metric():
    # COSINE and IP report a similarity (best hit highest); L2 a distance (best lowest).
    # A consumer that merges hits across collections and sorts highest-first must keep
    # the first-ranked hits, as it does against Milvus.
    mv = MockMilvus({})
    await mv.create_collection("cos")
    await mv.create_collection("l2", metric_type="l2")
    for name in ("cos", "l2", "implicit"):
        await mv.insert(name, [[1], [2], [3]])
    for name in ("cos", "implicit"):
        scores = [hit["distance"] for hit in (await mv.search(name, [[0]], top_k=3))[0]]
        assert scores == sorted(scores, reverse=True) and scores[0] == 0.95
    scores = [hit["distance"] for hit in (await mv.search("l2", [[0]], top_k=3))[0]]
    assert scores == [0.05, 0.06, 0.07]


async def test_milvus_delete_removes_only_listed_ids():
    mv = MockMilvus({})
    await mv.insert("c", [[1], [2]])
    assert await mv.delete("c", filter_expr="x > 1") == {"delete_count": 0}
    assert await mv.delete("c", ids=["c-0", "absent"]) == {"delete_count": 1}
    assert [h["id"] for h in (await mv.search("c", [[0]]))[0]] == ["c-1"]
