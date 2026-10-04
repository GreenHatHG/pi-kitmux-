#!/usr/bin/env bash
# One-shot check: static tools (ruff / codespell / vulture / mypy / pyright / pylint) + unit tests
set -euo pipefail
cd "$(dirname "$0")/.."
# The sandbox blocks writes to ~/.cache, so keep hook files inside the project
export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$PWD/.pre-commit-cache}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.tmp/uv-cache}"
uv run pre-commit run --all-files
uv run python -m unittest discover -s tests -v
