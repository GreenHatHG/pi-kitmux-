#!/usr/bin/env bash
# One-shot check: static tools (ruff / codespell / vulture / mypy / pyright / pylint) + unit tests
set -euo pipefail
cd "$(dirname "$0")/.."
# The sandbox blocks writes to ~/.cache, so keep hook files inside the project
export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$PWD/.pre-commit-cache}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.tmp/uv-cache}"
uv run pre-commit run --all-files
uv run python -m unittest discover -s tests -v
# kitty-tab-sync.ts has no Python surface to test; its title-resend behaviour is driven
# against a throwaway tmux server instead (needs node and tmux, both already required).
node --experimental-strip-types --test tests/test_title_resend.mjs
