"""Behavior contract for mock-service discovery and seed generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from perceivo._cli.mock_cmd import _SERVICE_MARKERS, _referenced_services
from perceivo.config import MOCKABLE_SERVICES
from perceivo.local.mocks.seed import DEFAULT_SEED, scaffold_seed

PUBLIC_CALL_SITES: dict[str, str] = {
    "llm": "perceivo.managed_service.ai.invoke",
    "database": "perceivo.managed_service.database.connect",
    "redis": "perceivo.cluster.redis.connect",
    "milvus": "perceivo.cluster.milvus.connect",
    "elasticsearch": "perceivo.cluster.elasticsearch.connect",
    "mcp": "perceivo.cluster.mcp.connect",
    "files": "perceivo.files.read",
    "secrets": "perceivo.secrets.get",
    "chat": "perceivo.alerts.send_direct",
    "inference": "perceivo.intelligence.inference.predict",
    "capabilities": "perceivo.capabilities.list",
}


def _project(tmp_path: Path, sources: dict[str, str]) -> Path:
    root = tmp_path / "project"
    components = root / "components"
    components.mkdir(parents=True)
    for name, body in sources.items():
        (components / f"{name}.py").write_text(body, encoding="utf-8")
    return root


def _call_site_source(service: str) -> str:
    return f"async def run(ctx):\n    return await {PUBLIC_CALL_SITES[service]}()\n"


def test_mock_service_lists_are_equal() -> None:
    assert set(_SERVICE_MARKERS) == set(MOCKABLE_SERVICES) == set(DEFAULT_SEED)


def test_scaffold_seed_emits_every_mockable_service() -> None:
    scaffold = scaffold_seed(sorted(MOCKABLE_SERVICES))
    assert set(scaffold) == set(MOCKABLE_SERVICES)


def test_every_mockable_service_has_a_public_call_site() -> None:
    assert set(PUBLIC_CALL_SITES) == set(MOCKABLE_SERVICES)


@pytest.mark.parametrize("service", sorted(PUBLIC_CALL_SITES))
def test_a_marker_matches_each_public_call_site(service: str) -> None:
    call_site = PUBLIC_CALL_SITES[service]
    assert any(marker in call_site for marker in _SERVICE_MARKERS[service])


def test_detector_finds_every_public_call_site(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {service: _call_site_source(service) for service in PUBLIC_CALL_SITES},
    )
    assert _referenced_services(root) == sorted(MOCKABLE_SERVICES)


@pytest.mark.parametrize("service", sorted(PUBLIC_CALL_SITES))
def test_each_call_site_is_attributed_to_one_service(tmp_path: Path, service: str) -> None:
    root = _project(tmp_path, {service: _call_site_source(service)})
    assert _referenced_services(root) == [service]


@pytest.mark.parametrize("service", sorted(PUBLIC_CALL_SITES))
def test_blind_marker_stops_service_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
) -> None:
    blinded = dict(_SERVICE_MARKERS)
    blinded[service] = ("perceivo.renamed_away",)
    monkeypatch.setattr("perceivo._cli.mock_cmd._SERVICE_MARKERS", blinded)

    root = _project(
        tmp_path,
        {name: _call_site_source(name) for name in PUBLIC_CALL_SITES},
    )
    detected = _referenced_services(root)

    assert service not in detected
    assert detected == sorted(set(MOCKABLE_SERVICES) - {service})
    assert set(scaffold_seed(detected)) == set(MOCKABLE_SERVICES) - {service}
