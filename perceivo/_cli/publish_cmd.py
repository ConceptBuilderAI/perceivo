"""perceivo publish: push a local orchestration project to Perceivo Cloud.

There is no flag for any credential: a secret on the command line lands in shell
history and the process list. The credential comes from ``PERCEIVO_*`` environment
variables (or the non-secret ``api:`` section of ``perceivo.yaml``), and
``--base-url`` has no environment fallback of its own, so a value from the
environment is never mistaken for an explicit argument.
"""

from __future__ import annotations

import asyncio
import json

import click

from ._refusals import refusals_as_errors


@click.command()
@click.argument("project_dir", default=".")
@click.option("--name", default=None, help="Override the orchestration name.")
@click.option(
    "--base-url",
    default=None,
    help="API base URL (default: $PERCEIVO_API_URL, then https://api.perceivo.ai). "
    "Never read from perceivo.yaml.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Build the bundle but don't POST it.",
)
def publish(
    project_dir: str,
    name: str | None,
    base_url: str | None,
    dry_run: bool,
):
    """Publish an orchestration project to Perceivo Cloud.

    Authenticates with an API key (PERCEIVO_API_KEY), an HMAC-signed API key
    (plus PERCEIVO_KEY_ID and PERCEIVO_SIGNING_SECRET), or a Microsoft Entra ID
    service principal (PERCEIVO_TENANT_ID, PERCEIVO_CLIENT_ID and
    PERCEIVO_CLIENT_SECRET or PERCEIVO_FEDERATED_TOKEN_FILE).
    """
    from ..orchestration.publish import publish as _publish

    with refusals_as_errors():
        result = asyncio.run(
            _publish(
                project_dir,
                name=name,
                base_url=base_url,
                dry_run=dry_run,
            )
        )

    if result.dry_run:
        click.echo("Dry run: bundle built, no upload performed.")
        click.echo(f"  components: {len(result.bundle.get('components', []))}")
        return

    click.echo(f"Published. orchestration_id={result.orchestration_id}")
    if result.response:
        click.echo(json.dumps(result.response, indent=2))
