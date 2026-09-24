#!/usr/bin/env python3
"""GitHub Pages 的建置腳本，輸出到 _site/。

跟 build_standalone.py 的差別：這裡不把資料內嵌進頁面，頁面直接讀 data/*.json。
所以每天更新報價只要換 JSON 檔，不必重建整頁，也不會有「快照過期」的問題。

只帶三個對外頁面。teardown.html 與 products.json 還在開發中，刻意不帶；
就算有人不小心把它們加進來，最後的檢查也會讓建置失敗，而不是悄悄發布出去。

    python3 scripts/build_site.py
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"

PUBLIC_PAGES = ["index.html", "rings.html", "signals.html"]
PUBLIC_DATA = ["chain.json", "quotes.json", "updates.json"]
NEVER_PUBLISH = ["teardown.html", "products.json"]


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)

    for name in PUBLIC_PAGES:
        shutil.copy2(ROOT / name, OUT / name)
    for name in PUBLIC_DATA:
        src = ROOT / "data" / name
        if src.exists():
            shutil.copy2(src, OUT / "data" / name)
        elif name != "updates.json":          # 跑馬燈可以沒有，其他不行
            sys.exit(f"缺少 data/{name}，中止建置")

    # 告訴 GitHub Pages 不要用 Jekyll 處理，檔案原樣提供
    (OUT / ".nojekyll").write_text("")

    # 最後一道防線：不公開的東西絕對不能出現在輸出裡，頁面也不能連過去
    leaked = [p.relative_to(OUT) for p in OUT.rglob("*") if p.name in NEVER_PUBLISH]
    if leaked:
        sys.exit(f"不公開的檔案出現在輸出中：{leaked}")
    for name in PUBLIC_PAGES:
        if 'href="teardown.html"' in (OUT / name).read_text(encoding="utf-8"):
            sys.exit(f"{name} 有連到尚未公開的 teardown.html")

    files = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file())
    size = sum((OUT / f).stat().st_size for f in files) / 1024
    print(f"_site/ 建置完成：{len(files)} 個檔案，{size:.0f} KB")
    for f in files:
        print("  ", f)


if __name__ == "__main__":
    main()
