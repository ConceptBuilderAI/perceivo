"""Unit tests for the org container resolver (``perceivo._internal``).

This pure-logic module (no IO) has one copy, internal; there is no public
``perceivo.storage.resolver``.

The fixture is kept parametrised rather than inlined: it is the shape that
makes a second copy cheap to re-pin, and the public surface omits these three
types (``StorageZone``, ``ZoneLocation``, ``OrgContainerResolver``) without
removing the logic, so a second *internal* consumer is a live possibility.
"""

from __future__ import annotations

import importlib

import pytest

_MODULES = [
    "perceivo._internal.storage_resolver",
]

ORG = "00000000-0000-4000-8000-000000000001"
SHORT = "00000000"


@pytest.fixture(params=_MODULES)
def mod(request):
    return importlib.import_module(request.param)


def test_short_id_first_eight_hex_lowercase(mod):
    assert mod.OrgContainerResolver.get_org_short_id(ORG.upper()) == SHORT
    # ORG's short id has no hex letters, so only a lettered id proves the lowercasing.
    lettered = "ABCDEF01-0000-4000-8000-000000000001"
    assert mod.OrgContainerResolver.get_org_short_id(lettered) == "abcdef01"


def test_org_container_name(mod):
    assert mod.OrgContainerResolver.get_org_container_name(ORG) == f"org-{ORG}"


@pytest.mark.parametrize(
    "zone,expected",
    [
        ("files", None),  # filled below
        ("intelligence", None),
        ("orchestrations", None),
        ("branding", None),
        ("_metadata", None),
    ],
)
def test_unified_zones_share_org_container(mod, zone, expected):
    assert mod.OrgContainerResolver.get_container_for_zone(ORG, zone) == f"org-{ORG}"


def test_milvus_and_es_dedicated_containers(mod):
    assert mod.OrgContainerResolver.get_container_for_zone(ORG, "milvus") == f"milvus-{SHORT}"
    assert (
        mod.OrgContainerResolver.get_container_for_zone(ORG, "elasticsearch")
        == f"es-snapshots-{SHORT}"
    )


def test_container_for_zone_accepts_enum(mod):
    zone = mod.StorageZone.MILVUS
    assert mod.OrgContainerResolver.get_container_for_zone(ORG, zone) == f"milvus-{SHORT}"


def test_prefix_for_unified_zone_has_trailing_slash(mod):
    assert mod.OrgContainerResolver.get_prefix_for_zone("files") == "files/"
    assert mod.OrgContainerResolver.get_prefix_for_zone("_metadata") == "_metadata/"


def test_prefix_for_milvus_is_empty_and_es_is_snapshots(mod):
    assert mod.OrgContainerResolver.get_prefix_for_zone("milvus") == ""
    assert mod.OrgContainerResolver.get_prefix_for_zone("elasticsearch") == "snapshots/"


def test_resolve_unified_zone(mod):
    loc = mod.OrgContainerResolver.resolve(ORG, "intelligence")
    assert loc.container == f"org-{ORG}"
    assert loc.prefix == "intelligence/"
    assert loc.is_dedicated is False


def test_resolve_dedicated_zone(mod):
    loc = mod.OrgContainerResolver.resolve(ORG, "milvus")
    assert loc.container == f"milvus-{SHORT}"
    assert loc.prefix == ""
    assert loc.is_dedicated is True


def test_zone_location_is_frozen(mod):
    loc = mod.OrgContainerResolver.resolve(ORG, "files")
    with pytest.raises(
        AttributeError
    ):  # frozen dataclass -> FrozenInstanceError (AttributeError subclass)
        loc.container = "mutated"  # frozen dataclass


def test_unknown_zone_raises_value_error(mod):
    with pytest.raises(ValueError):
        mod.OrgContainerResolver.get_container_for_zone(ORG, "not-a-zone")
    with pytest.raises(ValueError):
        mod.OrgContainerResolver.get_prefix_for_zone("not-a-zone")


def test_get_all_containers_for_org(mod):
    containers = mod.OrgContainerResolver.get_all_containers_for_org(ORG)
    assert containers == [
        f"org-{ORG}",
        f"milvus-{SHORT}",
        f"es-snapshots-{SHORT}",
        f"redis-backup-{SHORT}",
    ]


def test_get_unified_zones_contains_expected(mod):
    zones = set(mod.OrgContainerResolver.get_unified_zones())
    assert zones == {"files", "intelligence", "orchestrations", "branding", "_metadata"}


def test_get_all_zones_covers_every_enum_member(mod):
    assert set(mod.OrgContainerResolver.get_all_zones()) == {z.value for z in mod.StorageZone}


def test_metered_zones_exclude_trivial(mod):
    metered = set(mod.OrgContainerResolver.get_metered_zones())
    assert "branding" not in metered
    assert "_metadata" not in metered
    assert metered == {
        "files",
        "intelligence",
        "orchestrations",
        "milvus",
        "elasticsearch",
        "redis",
    }
