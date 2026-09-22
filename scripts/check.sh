#!/usr/bin/env bash
# 一键验证：静态检查（ruff / codespell / vulture / mypy / pyright / pylint）+ 单元测试
set -euo pipefail
cd "$(dirname "$0")/.."
# 沙箱禁止写 ~/.cache，hook 环境放在项目内
export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$PWD/.pre-commit-cache}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.tmp/uv-cache}"
uv run pre-commit run --all-files
uv run python -m unittest discover -s tests -v
