"""
Configuration for conversation channels.

Defines settings for conversation persistence, history TTL, and
Redis key prefixes used by the conversation channel infrastructure.
"""

from dataclasses import dataclass


@dataclass
class ConversationConfig:
    """
    Configuration for conversation channel persistence and behavior.

    Attributes:
        persistence: Backend for conversation history storage.
            - "loki": Write conversation entries to Loki (default, append-only).
            - "redis": Store in Redis with TTL-based expiration.
            - "none": No persistence (ephemeral streaming only).
        history_ttl_seconds: Time-to-live for Redis-persisted history entries.
            Only applies when persistence is "redis". Default: 86400 (24 hours).
        channel_prefix: Redis key prefix for channel pub/sub and queue keys.
            Default: "cb:conv".
    """

    persistence: str = "loki"
    history_ttl_seconds: int = 86400
    channel_prefix: str = "cb:conv"
