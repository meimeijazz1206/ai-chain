#!/bin/bash
# 每日更新的單一入口。把抓價、檢查、打包收成一支腳本，
# 排程只需要授權這一個指令，不必為每個步驟各按一次允許。
#
#   daily.sh check   抓最新收盤，跑自我檢查並列出今天該複查的三條關聯線
#   daily.sh build   再檢查一次，通過才重新打包三頁
#
# check 的離開碼就是 verify.py 的離開碼：非 0 代表資料有問題，不可上線。
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IDX=https://claude.ai/artifact/9h5qmHVR7wHaNMTNUeBDXh
RNG=https://claude.ai/artifact/JmjeR2UJ8w3hS19RiEDBRo
SIG=https://claude.ai/artifact/8c77F7dfPpjLNdPKBs6WMa

case "${1:-check}" in
  check)
    echo "▶ 抓最新收盤"
    python3 "$DIR/fetch_official.py" || exit 1
    echo
    echo "▶ 自我檢查"
    python3 "$DIR/verify.py" --rotate 3
    exit $?
    ;;
  build)
    echo "▶ 發布前再檢查一次"
    python3 "$DIR/verify.py" || { echo "檢查未通過，中止打包"; exit 1; }
    echo
    echo "▶ 重新打包"
    python3 "$DIR/build_standalone.py" --url-index "$IDX" --url-rings "$RNG" --url-signals "$SIG"
    ;;
  *)
    echo "用法：daily.sh [check|build]" >&2; exit 2
    ;;
esac
