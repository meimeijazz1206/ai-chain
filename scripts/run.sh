#!/bin/bash
# 每日更新台股 AI 供應鏈行情。沿用 tw-stock-screener 的 venv，不另外裝套件。
set -euo pipefail
VENV="$HOME/.claude/skills/tw-stock-screener/.venv/bin/python"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -x "$VENV" ]; then
  echo "找不到 venv：$VENV" >&2
  exit 1
fi

exec "$VENV" "$DIR/fetch_quotes.py" "$@"
