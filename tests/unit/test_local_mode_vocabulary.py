"""The local mode vocabulary, enforced identically on both entry points.

The same setting can be written two ways -- ``perceivo.local.set_mode(service,
mode)`` and ``mocks: {modes: {service: mode}}`` in ``perceivo.yaml`` -- and both
validate. If the second accepted any key and any value:

* ``modes: {redsi: local}`` would be accepted and do nothing. The run would
  proceed fully mocked and report success, which is the failure this whole plane
  exists to prevent: code shipped in the belief it was tested against a real
  store.
* ``modes: {mcp: local}`` would be accepted although ``mcp`` is limited to
  ``mock`` or ``disabled``. The run would fail later, at ``get_mcp_client()``,
  with an error about MCP rather than about the file that asked for it.
* ``default_mode: local`` would push *every* service to ``local`` including the
  two with no backend, and it names no service, so per-service validation cannot
  catch it.

``chat`` joins ``mcp`` in :data:`~perceivo.config.NO_LOCAL_BACKEND` here. It is
the sharper of the two cases: ``send_chat_message`` outside mock mode raises,
because there is no egress broker locally; returning
``{"status": "logged_locally"}`` would be a success-shaped object for a message
nobody received.

Nothing in this repository or the orchestration corpora sets either service to
``local``, so this tightens a path no configuration was using.
"""

from __future__ import annotations

import pytest

from perceivo import config as cfg
from perceivo.local import _modes
from perceivo.types.errors import ConfigurationError, PerceivoError


@pytest.fixture(autouse=True)
def _restore_loaded_config():
    """``load()`` writes a module global that ``config.get()`` reads.

    Every successful load below leaves that global pointing at a ``tmp_path``
    file, and this suite runs in random order, so without this any later test
    calling ``perceivo.config.get()`` would silently read one of these fixtures
    instead of loading its own.
    """
    before = cfg._LOADED
    yield
    cfg._LOADED = before


def _write(tmp_path, body: str):
    p = tmp_path / "perceivo.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_the_two_constants_have_one_home():
    """The plane reads the configuration's copy rather than keeping its own.

    Two copies is how the entry points came to disagree in the first place, so
    identity is asserted rather than equality: equal tuples would still pass
    the moment someone re-declared one of them here.
    """
    assert _modes.DECLARED_MODES is cfg.DECLARED_MODES
    assert _modes.NO_LOCAL_BACKEND is cfg.NO_LOCAL_BACKEND


def test_every_mockable_service_is_classified():
    """Non-vacuity: a service missing from both halves would be silently unruled."""
    assert set(cfg.MOCKABLE_SERVICES) >= cfg.NO_LOCAL_BACKEND
    for service in cfg.MOCKABLE_SERVICES:
        assert _modes.allowed_modes(service) in (
            cfg.DECLARED_MODES,
            ("mock", "disabled"),
        )


@pytest.mark.parametrize("service", sorted(cfg.NO_LOCAL_BACKEND))
def test_set_mode_refuses_local_where_there_is_no_backend(service):
    assert _modes.allowed_modes(service) == ("mock", "disabled")
    with pytest.raises(ValueError, match="no local backend"):
        _modes.to_internal(service, "local")


@pytest.mark.parametrize("service", sorted(cfg.NO_LOCAL_BACKEND))
def test_the_config_file_refuses_it_the_same_way(tmp_path, service):
    """The half that was missing. Same rule, same failure, at load rather than at the call."""
    path = _write(tmp_path, f"version: 1\nmocks:\n  modes:\n    {service}: local\n")
    with pytest.raises(ConfigurationError, match="no local backend"):
        cfg.load(path)


def test_a_mistyped_service_name_is_rejected_rather_than_ignored(tmp_path):
    path = _write(tmp_path, "version: 1\nmocks:\n  modes:\n    redsi: local\n")
    with pytest.raises(ConfigurationError, match="unknown service 'redsi'"):
        cfg.load(path)


def test_a_mode_outside_the_declared_three_is_rejected(tmp_path):
    """``byo-key`` is the configuration's *internal* spelling, not one a file may set."""
    path = _write(tmp_path, "version: 1\nmocks:\n  modes:\n    llm: byo-key\n")
    with pytest.raises(ConfigurationError, match="Valid modes"):
        cfg.load(path)


def test_default_mode_is_validated(tmp_path):
    path = _write(tmp_path, "version: 1\nmocks:\n  default_mode: reel\n")
    with pytest.raises(ConfigurationError, match="default_mode"):
        cfg.load(path)


def test_default_mode_local_cannot_reach_a_service_with_no_backend(tmp_path):
    """The case per-service validation structurally cannot catch.

    ``default_mode`` names no service, so the only place to stop it is where the
    mode is resolved. Asserted alongside a service that SHOULD go local, so a
    clamp that accidentally applied to everything would fail here.
    """
    path = _write(tmp_path, "version: 1\nmocks:\n  default_mode: local\n")
    config = cfg.load(path)
    for service in sorted(cfg.NO_LOCAL_BACKEND):
        assert config.service_mode(service) == "disabled"
    assert config.service_mode("redis") == "local"


def test_a_valid_file_still_loads(tmp_path):
    """The negative controls above prove nothing if every file raises."""
    path = _write(
        tmp_path,
        "version: 1\nredis:\n  enabled: true\n"
        "mocks:\n  modes:\n    milvus: local\n    mcp: disabled\n    chat: mock\n",
    )
    config = cfg.load(path)
    assert config.service_mode("milvus") == "local"
    assert config.service_mode("mcp") == "disabled"
    assert config.service_mode("chat") == "mock"
    assert config.service_mode("redis") == "local"
    assert config.service_mode("elasticsearch") == "mock"


def test_the_llm_still_resolves_to_its_internal_spelling(tmp_path):
    """The clamp must not have flattened the one service with a wider vocabulary."""
    path = _write(tmp_path, "version: 1\nai_foundry:\n  enabled: true\n")
    assert cfg.load(path).service_mode("llm") == "byo-key"
    assert _modes.to_internal("llm", "local") == "byo-key"
    assert _modes.to_declared("byo-key") == "local"


def test_the_rejection_is_catchable_as_a_perceivo_error(tmp_path):
    """Catching ``PerceivoError`` catches everything the SDK raises."""
    path = _write(tmp_path, "version: 1\nmocks:\n  modes:\n    mcp: local\n")
    with pytest.raises(PerceivoError):
        cfg.load(path)
