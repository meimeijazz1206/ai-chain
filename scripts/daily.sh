#!/bin/bash
# 本機手動更新的單一入口。把抓價、檢查、建置收成一支腳本。
# 每日排程已改由 GitHub Actions（抓價與發布）與 review.py（說法查證）負責，
# 這支只留給本機手動跑。
#
#   daily.sh check   抓最新收盤，跑自我檢查並列出今天該複查的三條關聯線
#   daily.sh build   再檢查一次，通過才建置 _site/（與 GitHub Pages 相同的輸出）
#
# check 的離開碼就是 verify.py 的離開碼：非 0 代表資料有問題，不可上線。
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    echo "▶ 建置前再檢查一次"
    python3 "$DIR/verify.py" || { echo "檢查未通過，中止建置"; exit 1; }
    echo
    echo "▶ 建置 _site/"
    python3 "$DIR/build_site.py"
    ;;
  *)
    echo "用法：daily.sh [check|build]" >&2; exit 2
    ;;
esac
