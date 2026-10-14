"""
perceivo CLI: command-line interface for local development.

    perceivo init                         # Initialize project
    perceivo new agent my_agent           # Scaffold a component
    perceivo validate component a.py a.yaml  # Validate a component
    perceivo validate orchestration .     # Validate an orchestration
    perceivo run component a.py           # Run a component
    perceivo run orchestration .          # Run an orchestration
"""

import logging
import sys

import click

from . import (
    export_cmd,
    import_cmd,
    init_cmd,
    mock_cmd,
    new_cmd,
    publish_cmd,
    run_cmd,
    validate_cmd,
)


def _route_logs_to_stderr() -> None:
    """Send perceivo runtime logs to stderr so stdout is a clean result channel.

    ``--json`` (and the human trace) must be the only thing on stdout so an agent
    can parse it. Pre-attaching a stderr handler also stops the local logging
    adapter from adding its own stdout handler.
    """
    log = logging.getLogger("perceivo")
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)


@click.group()
@click.version_option(package_name="perceivo")
def main():
    """perceivo — SDK for building AI orchestration components."""
    _route_logs_to_stderr()


main.add_command(export_cmd.export)
main.add_command(import_cmd.import_bundle)
main.add_command(init_cmd.init)
main.add_command(mock_cmd.mock)
main.add_command(new_cmd.new)
main.add_command(validate_cmd.validate)
main.add_command(run_cmd.run)
main.add_command(publish_cmd.publish)
