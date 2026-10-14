"""Publish an orchestration project to Perceivo Cloud.

The :func:`publish` function packages a local project directory (anything
:func:`export_orchestration` accepts) and POSTs it to ``/v1/orchestrations/import``.
The resulting orchestration is owned by the organization of the service account
the credential belongs to.

Authentication is one of three modes: API key, HMAC-signed API key, or a
Microsoft Entra ID service principal. It is passed as ``credentials=`` or
resolved from ``PERCEIVO_*`` environment variables and the non-secret ``api:``
section of the nearest ``perceivo.yaml`` at or above the project directory (not
the working directory's, which is what ``perceivo.config.load()`` reads).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .._bundle.orchestration import orchestration_to_bundle
from .._internal import transport
from .._internal.auth import resolve
from ..types._credentials import Credentials
from ..types.errors import PerceivoError

logger = logging.getLogger("perceivo.orchestration.publish")

#: The import route. A request signature covers this exact path, so a request
#: signed for any other path never verifies.
# Do not switch to the compatibility prefix: it is rewritten after signature verification.
IMPORT_PATH = "/v1/orchestrations/import"

#: A 5xx other than the one retryable shape may have been answered after the
#: import ran.
_UNSAFE_REMEDY = (
    "The import may have completed; check whether the orchestration exists before retrying."
)
_TOO_LARGE_REMEDY = (
    "A signed (HMAC) request may carry at most 1 MiB (1,048,576 bytes); API-key and "
    "Entra ID requests carry no such limit. Reduce the bundle or publish with another mode."
)


@dataclass(frozen=True)
class PublishResult:
    """Result of a ``publish()`` call.

    Attributes:
        orchestration_id: ID assigned by the platform (None for dry runs).
        bundle: The export bundle that was sent (or would have been sent).
        status_code: HTTP status code returned by the API (None for dry runs).
        response: Parsed JSON response body, when available.
        dry_run: Whether this was a dry run.
    """

    orchestration_id: Optional[str]
    bundle: dict[str, Any]
    status_code: Optional[int]
    response: Optional[dict[str, Any]]
    dry_run: bool


class PublishError(PerceivoError, RuntimeError):
    """The import endpoint refused the bundle.

    The message carries the **whole** refusal: the error code, the message and
    every failing check id with its message. An author needs the check id to
    find the rule, and truncating the raw body hid it behind the first 512
    characters of a validation report.

    Attributes:
        status_code: HTTP status code returned by the API, or ``None`` when the
            bundle was refused locally before anything was sent.
        body: Parsed JSON response body, or ``None`` when it was not JSON.
        text: The raw response body.
        checks: The failing checks (``{"id", "severity", "title", "message"}``
            dicts) found in the response, in response order.
        remedy: What to change, when there is one specific thing.
    """

    def __init__(
        self,
        status_code: int | None,
        body: Any,
        text: str,
        *,
        remedy: str | None = None,
        summary: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.text = text
        self.remedy = remedy
        described, self.checks = _describe_error(body, text)
        if status_code is None:
            lines = [f"publish: {summary or described}"]
        else:
            lines = [f"publish: API returned {status_code}: {summary or described}"]
        lines.extend(f"  {_format_check(check)}" for check in self.checks)
        if remedy:
            lines.append(remedy)
        super().__init__("\n".join(lines))


#: Keys of a structured error ``detail`` that :func:`_describe_error` renders
#: itself; any other key is appended verbatim so nothing the server said is lost.
_RENDERED_DETAIL_KEYS = ("error", "message", "validation_report", "errors")


def _describe_error(body: Any, text: str) -> tuple[str, list[dict[str, Any]]]:
    """Split an error response into a one-line summary and its failing checks.

    Shapes handled: FastAPI ``{"detail": "..."}``; a 422 ``{"detail": [...]}``;
    a structured ``{"detail": {"error", "message", ...}}`` carrying either a
    component ``validation_report`` (``checks``) or a graph ``errors`` list.
    Anything else falls back to the raw body, untruncated.
    """
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, str):
        return detail, []
    if isinstance(detail, list):
        return "request rejected", [_as_check(item) for item in detail]
    if not isinstance(detail, dict):
        return text or "(empty response body)", []

    head = ": ".join(str(detail[key]) for key in ("error", "message") if detail.get(key))
    checks: list[dict[str, Any]] = []
    report = detail.get("validation_report")
    if isinstance(report, dict):
        checks.extend(
            _as_check(check)
            for check in report.get("checks") or []
            if not (isinstance(check, dict) and check.get("severity") == "pass")
        )
    errors = detail.get("errors")
    if isinstance(errors, list):
        checks.extend(_as_check(item) for item in errors)
    extras = {k: v for k, v in detail.items() if k not in _RENDERED_DETAIL_KEYS}
    if extras:
        rendered = json.dumps(extras, sort_keys=True, default=str)
        head = f"{head} {rendered}" if head else rendered
    if not head:
        head = "request rejected" if checks else json.dumps(detail, sort_keys=True, default=str)
    return head, checks


def _as_check(item: Any) -> dict[str, Any]:
    """Normalise one reported problem to ``{"id", "severity", "title", "message"}``."""
    if not isinstance(item, dict):
        return {"id": None, "severity": None, "title": None, "message": str(item)}
    message = item.get("message") or item.get("msg") or ""
    loc = item.get("loc")
    if loc and not item.get("message"):
        message = f"{'.'.join(str(part) for part in loc)}: {message}"
    return {
        "id": item.get("id") or item.get("code") or item.get("check_id"),
        "severity": item.get("severity"),
        "title": item.get("title"),
        "message": message or json.dumps(item, sort_keys=True, default=str),
    }


def _format_check(check: dict[str, Any]) -> str:
    parts = []
    if check.get("id"):
        parts.append(f"[{check['id']}]")
    if check.get("severity"):
        parts.append(f"{check['severity']}:")
    if check.get("title"):
        parts.append(f"{check['title']} --")
    parts.append(str(check.get("message") or ""))
    return " ".join(parts)


async def publish(
    project_dir: str,
    *,
    name: Optional[str] = None,
    credentials: Optional[Credentials] = None,
    base_url: Optional[str] = None,
    dry_run: bool = False,
) -> PublishResult:
    """Publish a local orchestration project to Perceivo Cloud.

    The credential and base URL come from ``credentials=``/``base_url=`` when
    given, then ``PERCEIVO_*`` environment variables, then the ``api:`` section
    of the nearest ``perceivo.yaml`` at or above ``project_dir``. That is not the
    working directory's file, which is what ``perceivo.config.load()`` reads.
    The default base URL is ``https://api.perceivo.ai``.

    Args:
        project_dir: Local orchestration project directory (contains
            ``orchestration.yaml`` plus component scripts/manifests).
        name: Optional name override for the published orchestration.
            When omitted, the name in ``orchestration.yaml`` is used.
        credentials: A :class:`perceivo.types.Credentials`. When given it alone
            decides the authentication mode.
        base_url: API base URL. Falls back to ``PERCEIVO_API_URL``, then
            ``https://api.perceivo.ai``. It is never read from ``perceivo.yaml``,
            and a file that names it is refused.
        dry_run: When ``True``, build the bundle but do not POST.

    Returns:
        A :class:`PublishResult` describing the outcome.

    Raises:
        FileNotFoundError: If ``project_dir`` has no orchestration.yaml.
        perceivo.types.ConfigurationError: No credential, a conflicting or
            incomplete one, a secret or a base URL in ``perceivo.yaml``, or an
            ``http://`` URL.
        perceivo.types.AuthenticationError: The API answered 401, or Entra ID
            refused the token request.
        perceivo.types.AccessDeniedError: The API answered 403.
        perceivo.types.ServiceError: The API answered 5xx or did not answer.
            ``retryable`` is ``True`` only when nothing was done.
        PublishError: Any other refusal, including a 2xx without ``asset_id``.
            It subclasses ``RuntimeError`` and its message lists every failing
            check.
    """
    path = Path(project_dir).resolve()
    # The platform OrchestrationExport bundle, the shape /orchestrations/import accepts.
    bundle = orchestration_to_bundle(path)
    if name:
        bundle.setdefault("orchestration", {})["name"] = name
        bundle["new_name"] = name

    if dry_run:
        logger.info("publish(dry_run=True): bundle ready for %s", path)
        return PublishResult(
            orchestration_id=None,
            bundle=bundle,
            status_code=None,
            response=None,
            dry_run=True,
        )

    auth = resolve(project_dir=path, credentials=credentials, base_url=base_url)
    # Serialized once: these bytes are the bytes sent and, in HMAC mode, signed.
    payload = json.dumps(bundle, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    logger.info("Publishing %s -> %s%s (%s)", path, auth.base_url, IMPORT_PATH, auth.mode)
    try:
        resp = await transport.request(
            auth, "POST", IMPORT_PATH, body=payload, unsafe_remedy=_UNSAFE_REMEDY
        )
    except transport.BodyTooLargeError as exc:
        raise PublishError(
            None, None, "", summary=f"refused before sending: {exc}", remedy=_TOO_LARGE_REMEDY
        ) from None

    try:
        body: Optional[dict[str, Any]] = resp.json()
    except ValueError:
        body = None

    if not 200 <= resp.status_code < 300:
        remedy = _TOO_LARGE_REMEDY if resp.status_code == 413 else None
        raise PublishError(resp.status_code, body, resp.text, remedy=remedy)

    # The import endpoint answers with ``ImportResult``, which names the new
    # orchestration ``asset_id``. That key is the contract; nothing else is read.
    orch_id = body.get("asset_id") if isinstance(body, dict) else None
    if not isinstance(orch_id, str) or not orch_id:
        raise PublishError(
            resp.status_code,
            body,
            resp.text,
            summary="the response carried no asset_id, so the published orchestration "
            "cannot be identified",
        )

    return PublishResult(
        orchestration_id=orch_id,
        bundle=bundle,
        status_code=resp.status_code,
        response=body,
        dry_run=False,
    )
