"""Bundle refusals as a CLI error, not a traceback.

Building a bundle refuses a project for what is in the user's own files: a
malformed record, a missing script or nested project, a field of the wrong
type, YAML that does not parse. Those are answers, not crashes, so the CLI
prints each as ``Error: <reason>`` and exits 1. Any other exception is a defect
in the SDK and keeps its traceback.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import click


def _one_line(text: str) -> str:
    return " ".join(text.split())


@contextmanager
def refusals_as_errors() -> Iterator[None]:
    """Turn a refusal raised in the block into ``click.ClickException``."""
    import yaml
    from pydantic import ValidationError

    from ..types.errors import PerceivoError

    try:
        yield
    except ValidationError as exc:
        # The default text spans lines and ends each error with a docs URL.
        reasons = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or exc.title}: {err['msg']}"
            for err in exc.errors()
        )
        raise click.ClickException(_one_line(f"{exc.title} is invalid: {reasons}")) from exc
    except yaml.YAMLError as exc:
        raise click.ClickException(_one_line(str(exc))) from exc
    except FileNotFoundError as exc:
        # An OS-raised one quotes the path as a repr, doubling Windows backslashes.
        text = f"{exc.strerror}: {exc.filename}" if exc.filename else str(exc)
        raise click.ClickException(text) from exc
    except PerceivoError as exc:
        # As written: a publish refusal lists the server's failing checks a line each.
        raise click.ClickException(str(exc)) from exc
