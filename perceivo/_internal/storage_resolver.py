"""Organization container resolver for unified storage.

Maps (org_id, zone) pairs to Azure Blob Storage container names and blob
prefixes.

Container strategy:
  - ``org-{org_id}``: unified container for files, intelligence,
    orchestrations, branding, and metadata.
  - ``milvus-{org_short_id}``: dedicated container for Milvus vector data
    (Milvus requires exclusive ownership of the container).
  - ``es-snapshots-{org_short_id}``: dedicated container for Elasticsearch
    snapshot/restore data.
  - ``redis-backup-{org_short_id}``: dedicated container for Redis
    backup/restore data.

``org_short_id`` is the first 8 hex characters of the UUID with hyphens
removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StorageZone(str, Enum):
    """Well-known storage zones within an organization."""

    FILES = "files"
    INTELLIGENCE = "intelligence"
    ORCHESTRATIONS = "orchestrations"
    BRANDING = "branding"
    METADATA = "_metadata"
    MILVUS = "milvus"
    ELASTICSEARCH = "elasticsearch"
    REDIS = "redis"


@dataclass(frozen=True)
class ZoneLocation:
    """Resolved storage location for a zone."""

    container: str
    prefix: str
    is_dedicated: bool  # True if the zone has its own container


_UNIFIED_ZONES = frozenset(
    {
        StorageZone.FILES,
        StorageZone.INTELLIGENCE,
        StorageZone.ORCHESTRATIONS,
        StorageZone.BRANDING,
        StorageZone.METADATA,
    }
)


class OrgContainerResolver:
    """Resolves organization storage paths to Azure Blob locations.

    Handles the hybrid container strategy: unified org container for
    files/intelligence/orchestrations/branding, dedicated containers
    for infrastructure services (Milvus, ES, Redis).

    Example::

        resolver = OrgContainerResolver()
        loc = resolver.resolve("00000000-0000-4000-8000-000000000001", "intelligence")
        # loc.container == "org-00000000-0000-4000-8000-000000000001"
        # loc.prefix == "intelligence/"
    """

    ORG_SHORT_ID_LENGTH = 8  # first 8 hex chars of UUID (no hyphens)

    @staticmethod
    def get_org_short_id(org_id: str) -> str:
        """Return first 8 hex characters of the org UUID (hyphens removed).

        Used for infrastructure container naming where shorter names are
        preferred (Azure container names must be 3-63 chars).

        Args:
            org_id: Full organization UUID string.

        Returns:
            8-character lowercase hex string.
        """
        return str(org_id).replace("-", "")[: OrgContainerResolver.ORG_SHORT_ID_LENGTH].lower()

    @staticmethod
    def get_org_container_name(org_id: str) -> str:
        """Return the unified org container name: ``org-{full_uuid}``.

        Azure container names must be 3-63 lowercase alphanumeric characters and
        hyphens; ``org-`` plus a 36-character UUID is 40.
        """
        return f"org-{str(org_id).lower()}"

    @staticmethod
    def get_container_for_zone(org_id: str, zone: str) -> str:
        """Return the Azure Blob container name for a given zone.

        Args:
            org_id: Full organization UUID string.
            zone: Storage zone name (e.g. ``"files"``, ``"milvus"``).

        Returns:
            Container name string.

        Raises:
            ValueError: If the zone is unknown.
        """
        zone_enum = StorageZone(zone) if not isinstance(zone, StorageZone) else zone

        if zone_enum in _UNIFIED_ZONES:
            return OrgContainerResolver.get_org_container_name(org_id)

        short_id = OrgContainerResolver.get_org_short_id(org_id)
        if zone_enum == StorageZone.MILVUS:
            return f"milvus-{short_id}"
        if zone_enum == StorageZone.ELASTICSEARCH:
            return f"es-snapshots-{short_id}"
        if zone_enum == StorageZone.REDIS:
            return f"redis-backup-{short_id}"

        raise ValueError(f"Unknown zone: {zone}")

    @staticmethod
    def get_prefix_for_zone(zone: str) -> str:
        """Return the blob prefix within the container for a given zone.

        Args:
            zone: Storage zone name.

        Returns:
            Prefix string (includes trailing ``/``), or ``""`` for zones
            that own the entire container.
        """
        zone_enum = StorageZone(zone) if not isinstance(zone, StorageZone) else zone

        if zone_enum in _UNIFIED_ZONES:
            return f"{zone_enum.value}/"
        if zone_enum == StorageZone.MILVUS:
            return ""  # Milvus owns entire container
        if zone_enum == StorageZone.ELASTICSEARCH:
            return "snapshots/"
        if zone_enum == StorageZone.REDIS:
            return ""  # Redis owns entire container

        raise ValueError(f"Unknown zone: {zone}")

    @staticmethod
    def resolve(org_id: str, zone: str) -> ZoneLocation:
        """Resolve a zone to its full storage location.

        Args:
            org_id: Full organization UUID string.
            zone: Storage zone name.

        Returns:
            :class:`ZoneLocation` with container, prefix, and dedicated flag.
        """
        zone_enum = StorageZone(zone) if not isinstance(zone, StorageZone) else zone
        return ZoneLocation(
            container=OrgContainerResolver.get_container_for_zone(org_id, zone_enum),
            prefix=OrgContainerResolver.get_prefix_for_zone(zone_enum),
            is_dedicated=zone_enum not in _UNIFIED_ZONES,
        )

    @staticmethod
    def get_all_containers_for_org(org_id: str) -> list[str]:
        """Return all container names associated with an org.

        Used for data export and complete metering.  Infrastructure
        containers are included even if the services are not provisioned
        (the caller should handle missing containers gracefully).

        Args:
            org_id: Full organization UUID string.

        Returns:
            List of container name strings.
        """
        short_id = OrgContainerResolver.get_org_short_id(org_id)
        return [
            OrgContainerResolver.get_org_container_name(org_id),
            f"milvus-{short_id}",
            f"es-snapshots-{short_id}",
            f"redis-backup-{short_id}",
        ]

    @staticmethod
    def get_unified_zones() -> list[str]:
        """Return the zone names that live in the unified org container."""
        return [z.value for z in _UNIFIED_ZONES]

    @staticmethod
    def get_all_zones() -> list[str]:
        """Return all zone names."""
        return [z.value for z in StorageZone]

    @staticmethod
    def get_metered_zones() -> list[str]:
        """Return the zones included in storage billing.

        Excludes ``_metadata`` and ``branding``.
        """
        return [
            StorageZone.FILES.value,
            StorageZone.INTELLIGENCE.value,
            StorageZone.ORCHESTRATIONS.value,
            StorageZone.MILVUS.value,
            StorageZone.ELASTICSEARCH.value,
            StorageZone.REDIS.value,
        ]
