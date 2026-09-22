#!/usr/bin/env bash
# 一键验证：静态检查（ruff / codespell / vulture / mypy / pyright / pylint）+ 单元测试
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pre-commit run --all-files
uv run python -m unittest discover -s tests -v
