#!/usr/bin/env python3
"""把頁面與 data/*.json 打包成單一檔案，用來分享或上傳。

兩個頁面（地層剖面 index.html、爆炸環 rings.html）都會處理，各輸出兩種版本：
    dist/<name>.standalone.html  完整 HTML，雙擊可開
    dist/<name>.artifact.html    去掉外層標籤，給 Artifact 用

資料變成內嵌快照不會再更新，所以頁首會加一條說明帶寫清楚資料日期。

兩頁互相切換的連結預設是相對路徑，只在同一個資料夾內有效。
上傳成兩個獨立網址時，用參數把連結換成絕對網址：

    python3 scripts/build_standalone.py --url-index https://... --url-rings https://...
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PAGES = {"index": "index.html", "rings": "rings.html", "signals": "signals.html"}

STRIP = ('<!doctype html>', '<html lang="zh-Hant">', "<head>", "</head>",
         "<body>", "</body>", "</html>", '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">')


def data_tags():
    chain = json.loads((ROOT / "data" / "chain.json").read_text(encoding="utf-8"))
    quotes = json.loads((ROOT / "data" / "quotes.json").read_text(encoding="utf-8"))

    def blob(obj, tag_id):
        # </ 必須跳脫，否則 JSON 裡的字串會提前關掉 script 標籤
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        return f'<script type="application/json" id="{tag_id}">{raw}</script>'

    stamp = quotes.get("updatedAt", "未知")
    days = sorted({q["asOf"] for q in quotes["quotes"].values() if q.get("asOf")})
    as_of = days[-1] if days else "未知"
    tags = blob(chain, "chain-data") + "\n" + blob(quotes, "quotes-data")
    upd = ROOT / "data" / "updates.json"
    if upd.exists():   # 跑馬燈的最新查證
        tags += "\n" + blob(json.loads(upd.read_text(encoding="utf-8")), "updates-data")
    return tags, stamp, as_of


def build(name, fragment, tags, stamp, as_of, links):
    html = (ROOT / PAGES[name]).read_text(encoding="utf-8")

    banner = (
        '<div class="snapshot"><b>這是靜態快照，價格不會自動更新。</b>'
        f'行情擷取於 {stamp}，收盤價為 {as_of} 這個交易日。'
        '關聯線與評分為公開資訊整理與主觀判斷，僅供研究，非投資建議。</div>'
    )
    if ".snapshot{" not in html:      # rings.html 沒有這條樣式，補上
        banner_css = ("<style>.snapshot{background:#FFF6E8;border:1px solid #E8C98A;border-radius:8px;"
                      "padding:10px 14px;font-size:14px;color:#6B4E12;line-height:1.6;"
                      "max-width:1720px;margin:0 auto 16px;margin-left:28px;margin-right:28px}"
                      ".snapshot b{font-weight:700}</style>")
        banner = banner_css + banner

    html = html.replace("</header>", "</header>\n" + banner, 1)
    html = html.replace("<script>\nconst S", tags + "\n<script>\nconst S", 1)
    html = html.replace("<script>\nconst $=", tags + "\n<script>\nconst $=", 1)

    for page, url in links.items():
        if url:
            html = html.replace(f'href="{PAGES[page]}"', f'href="{url}" target="_top" rel="noopener"')

    if fragment:
        for tag in STRIP:
            html = html.replace(tag, "")
        html = re.sub(r"\n{3,}", "\n\n", html).strip() + "\n"

    DIST.mkdir(exist_ok=True)
    out = DIST / f"{name}.{'artifact' if fragment else 'standalone'}.html"
    out.write_text(html, encoding="utf-8")
    assert "chain-data" in html, f"{out} 沒有嵌到資料"
    print(f"{out.name:32} {len(html)/1024:5.0f} KB")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-index")
    ap.add_argument("--url-rings")
    ap.add_argument("--url-signals")
    a = ap.parse_args()
    links = {"index": a.url_index, "rings": a.url_rings, "signals": a.url_signals}

    # 三頁互相連結。少給一個網址，該連結上線後會指向不存在的相對路徑
    missing = [k for k, v in links.items() if not v]
    if missing and any(links.values()):
        print(f"警告：{'、'.join(missing)} 沒給網址，這些連結上線後會斷")

    tags, stamp, as_of = data_tags()
    print(f"行情 {stamp}　收盤日 {as_of}")
    for name in PAGES:
        for frag in (True, False):
            build(name, frag, tags, stamp, as_of, links)
