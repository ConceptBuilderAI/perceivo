"""perceivo init: initialize a project with perceivo.yaml and .env."""

from pathlib import Path

import click


@click.command()
@click.option("--dir", "directory", default=".", help="Directory to initialize (default: current).")
def init(directory: str):
    """Initialize a perceivo project with config files."""
    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=True)

    config_file = path / "perceivo.yaml"
    if config_file.exists():
        click.echo(f"  perceivo.yaml already exists in {path}")
    else:
        config_file.write_text(
            _PERCEIVO_YAML_TEMPLATE,
            encoding="utf-8",
        )
        click.echo(f"  Created {config_file}")

    env_file = path / ".env"
    if env_file.exists():
        click.echo(f"  .env already exists in {path}")
    else:
        env_file.write_text(
            _ENV_TEMPLATE,
            encoding="utf-8",
        )
        click.echo(f"  Created {env_file}")

    click.echo()
    click.echo("Project initialized. Edit .env to add your API keys.")


_PERCEIVO_YAML_TEMPLATE = """\
# perceivo.yaml — Local development configuration
version: 1

# The local-runner home defaults to .perceivo/ beside this file. A top-level
# root: (relative to this file) or PERCEIVO_HOME moves it.
# files:
#   working_dir: ./working_files  # relative to this file; default <home>/files/working
#   org_dir: ./org_files          # relative to this file; default <home>/files/org

secrets:
  source: dotenv
  dotenv_path: .env

redis:
  enabled: false
  url: redis://localhost:6379

# HTTP: use httpx or provider SDKs directly for external API calls.
# Retrieve API keys with: api_key = await perceivo.secrets.get("MY_KEY")

logging:
  level: INFO
  format: console
"""

_ENV_TEMPLATE = """\
# perceivo local development secrets
# Add your API keys and secrets here.
# These are loaded by perceivo.secrets.get("key_name")

# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
"""
