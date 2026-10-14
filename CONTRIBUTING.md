# Contributing to Perceivo SDK

Thank you for your interest in contributing to the Perceivo SDK. This document
explains how to get set up, our expectations for contributions, and how changes
are reviewed.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](./CODE_OF_CONDUCT.md).
Please report unacceptable behavior to conduct@perceivo.ai.

## Ways to Contribute

- **Report bugs** - open an issue using the bug report template.
- **Request features** - open an issue using the feature request template.
- **Improve documentation** - corrections to
  [perceivo.ai/docs](https://perceivo.ai/docs) are welcome.
- **Submit code** - fix a bug or implement a feature through a pull request.

## Development Setup

```bash
# Clone your fork
git clone https://github.com/<your-username>/perceivo.git
cd perceivo

# Create a virtual environment and install the development tools
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]" build twine types-PyYAML
```

Use Python 3.11, 3.12, or 3.13.

## Before You Open a Pull Request

- **Repository integrity** - run
  `python scripts/verify_public_tree.py verify --root .`.
- **Format and lint** - run `python -m ruff check perceivo tests scripts`
  and `python -m ruff format --check perceivo tests scripts`.
- **Type-check** - run the two commands CI runs, with zero diagnostics:
  `python -m mypy --explicit-package-bases perceivo tests/unit
  scripts/verify_public_tree.py scripts/check_release_tag.py
  scripts/check_workflow_policy.py scripts/compare_index_artifacts.py
  scripts/check_release_settings.py` and `python -m mypy --strict tests/typing`.
- **Test** - add tests for new behavior and run `python -m pytest -q`; the
  repository configuration enforces the coverage floor.
- **Build** - run `python -m build` and `python -m twine check dist/*`.
- **Docstrings** - give every public symbol a clear, accurate docstring.
- **Changelog** - add an entry under an `## [Unreleased]` heading at the top
  of [CHANGELOG.md](./CHANGELOG.md).
- **Keep it focused** - submit one logical change per pull request.

## Pull Request Process

1. Fork the repository and create a topic branch from the default branch.
2. Make your change, with tests and documentation.
3. Ensure repository integrity, lint, format, type-check, tests, and build all
   pass locally. CI runs the same gates.
4. Open a pull request using the template and describe the motivation and
   approach.
5. A maintainer will review. Address feedback with additional commits on your
   branch.

## Commit Messages

Write clear, imperative commit messages ("Add X", "Fix Y"). Reference related
issues where relevant, for example `Fixes #123`.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](./LICENSE), the same license that covers this project.
