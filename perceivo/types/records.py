"""Define the plane-owned result types: the shapes verbs return.

These types mirror the platform's wire contract, so a field means the same
thing on both sides of the wire.

A result type that belongs to one plane lives on that plane rather than here:
``BlobEntry`` at :mod:`perceivo.files._types`, ``ReviewResult`` at
:mod:`perceivo.reviews`, and ``AlertDelivery`` at :mod:`perceivo.alerts`.

Timestamps are ``str``: each arrives as ISO-8601 text already serialized by
the route.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, Field, dataclass, field, fields
from typing import Any, ClassVar, Protocol, TypeVar

from .errors import ServiceError

__all__ = [
    "FileEntry",
    "ModelPricing",
    "ParamDeclaration",
    "Prediction",
    "SecretDeclaration",
]


@dataclass(frozen=True)
class FileEntry:
    """One entry in the org file store.

    Returned by ``perceivo.files`` listing, folder creation, rename and move.
    ``write()`` returns ``{entry, version}`` instead.

    The last six fields have defaults because the local runtime does not emit
    them.
    """

    id: str
    name: str
    path: str
    #: ``"file"`` or ``"folder"``. A plain ``str``, so a new server-side value
    #: does not raise.
    entry_type: str
    #: ``None`` at the root of a space.
    parent_id: str | None
    #: ``None`` for folders.
    size_bytes: int | None
    #: ``None`` for folders.
    mime_type: str | None
    #: ``None`` for folders.
    blob_name: str | None
    #: ISO-8601.
    created_at: str
    #: ISO-8601.
    updated_at: str

    # Production only: the local adapter emits none of these six, so each must
    # carry a default.
    storage_space_id: str | None = None
    is_starred: bool | None = None
    is_trashed: bool | None = None
    trashed_at: str | None = None
    created_by_user_id: str | None = None
    updated_by_user_id: str | None = None


@dataclass(frozen=True)
class Prediction:
    """The result of ``intelligence.inference.predict``.

    All four fields are always present, and none is ``Optional``.
    """

    #: The model's own output, in the deployed model's type. A dict output may
    #: carry a nested ``latency_ms``.
    prediction: Any
    #: Never absent and never ``None``.
    model_id: str
    #: Defaults to ``{}``, never ``None``.
    metadata: dict[str, Any]
    #: Always set.
    correlation_id: str


@dataclass(frozen=True)
class ModelPricing:
    """A model's rate card.

    All six keys are always present and no value is ever ``None``. A model with
    no recorded rate arrives as ``0.0``, indistinguishable from a free model; an
    unpriced or inactive model makes ``cost.model_pricing`` return ``None``
    instead.

    The unit is USD per one million tokens, and the field names carry it.

    The two ``our_cost_*`` fields are Perceivo's own provider cost.
    """

    provider: str
    model_name: str
    #: Customer-facing, USD per 1M tokens.
    input_price_per_1m_usd: float
    #: Customer-facing, USD per 1M tokens.
    output_price_per_1m_usd: float
    #: Perceivo's own provider cost, USD per 1M tokens.
    our_cost_input_per_1m_usd: float
    #: Perceivo's own provider cost, USD per 1M tokens.
    our_cost_output_per_1m_usd: float


@dataclass(frozen=True)
class ParamDeclaration:
    """What a component declared about one input parameter.

    The manifest entry reaches the component verbatim, with no key whitelisted,
    validated or stripped. ``type``, ``default``, ``description`` and
    ``constraints`` are published in the manual's configuration chapter.

    ``extra`` keeps any key this class does not name, so no information the
    author wrote is lost. :class:`SecretDeclaration` has no such field; see its
    docstring.

    ``default`` is ``None`` both when the key is absent and when it is declared
    as null. Parameter resolution reads the raw dict, so behaviour is
    unaffected.
    """

    #: The dict key. Always present.
    name: str
    #: One of the nine input types. Defaults to ``"text"`` when omitted.
    type: str = "text"
    #: Set by every shipped manifest, but not enforced at runtime.
    label: str | None = None
    description: str | None = None
    options: list[str] | None = None
    #: See the class docstring caveat.
    default: Any = None
    required: bool | None = None
    #: Read by the Inspector.
    constraints: dict[str, Any] | None = None
    depends_on: dict[str, Any] | None = None
    #: Every key the manifest carried that is not named above.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecretDeclaration:
    """What a component declared about one required secret.

    This is the only closed declaration type. A declaration is never a value,
    but nothing on the wire enforces that, so a manifest with an inline
    ``value:`` under ``required_secrets`` would otherwise hand a live credential
    to component code. Only the four known keys are kept and the rest are
    dropped; there is deliberately no ``extra`` field.
    """

    #: The alias passed to ``perceivo.secrets.get``. Always present.
    name: str
    #: Not enforced at runtime.
    label: str | None = None
    description: str | None = None
    #: Defaults to ``True``, matching how secret resolution reads an absent key.
    required: bool = True


#: Field names and required subsets cached once; calls build records in bulk.
_FIELDS: dict[type, frozenset[str]] = {}
_REQUIRED: dict[type, frozenset[str]] = {}


class _DataclassRecord(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]


_RecordT = TypeVar("_RecordT", bound=_DataclassRecord)


def _from_wire(
    cls: type[_RecordT], raw: Mapping[str, Any], *, collect_extra: bool = False
) -> _RecordT:
    """Build one record from a response dict.

    Unknown keys are dropped, so a server-side addition is harmless and, for
    :class:`SecretDeclaration`, inline credentials stay out of component code.
    A missing required key is a fault: the backing service disagreed with its
    own contract, and a default would hand the component a value nobody sent.
    Fields that may genuinely be absent carry a dataclass default.

    ``collect_extra`` keeps unknown keys instead, for :class:`ParamDeclaration`
    only.

    Raises:
        ServiceError: The response omits a key the type requires. Raised
            instead of the dataclass ``TypeError`` so that ``except
            PerceivoError`` catches it. Not retryable.
    """
    known = _FIELDS.get(cls)
    if known is None:
        known = _FIELDS[cls] = frozenset(f.name for f in fields(cls)) - {"extra"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    if collect_extra:
        kwargs["extra"] = {k: v for k, v in raw.items() if k not in known}
    required = _REQUIRED.get(cls)
    if required is None:
        required = _REQUIRED[cls] = frozenset(
            f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING
        )
    missing = required - kwargs.keys()
    if missing:
        raise ServiceError(
            f"{cls.__name__} response is missing "
            f"{', '.join(repr(k) for k in sorted(missing))}; "
            f"got {sorted(raw)}"
        )
    return cls(**kwargs)
