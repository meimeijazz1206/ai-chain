#!/usr/bin/env python3
"""一次性補齊近三個月累計年增需要的月營收，寫進 data/revenue.json。

近三個月累計年增要六個數字：今年最近三個月、去年同期三個月。
每日的 OpenAPI 只給最新月、上月、去年同月，缺最新月往前第二個月，以及去年同期的另外兩個月。
這支從公開資訊觀測站的月營收彙總頁補，每一頁同時給當月與去年同月營收，
所以只要補兩個月；上市上櫃各分國內與外國企業兩頁，共八頁。補完之後每個月的 OpenAPI 就能自己接下去。

會先跑一次 fetch_official.py 確定最新月份，已經有的數字不覆蓋，可以重跑。

    python3 scripts/backfill_revenue.py
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_official import DATA, company_codes, month_shift, num   # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (ai-chain research site)"}
GAP = 3.0


def page(market, ym, kind):
    """公開資訊觀測站月營收彙總頁 → {代號: (當月營收, 去年當月營收)}，單位千元。
    kind 0 是國內公司，1 是外國企業（KY 公司放在這一頁，漏抓會少掉世芯、訊芯這些）。"""
    roc_y, m = int(ym[:4]) - 1911, int(ym[5:])
    url = f"https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc_y}_{m}_{kind}.html"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        html = r.read().decode("cp950", errors="replace")
    out = {}
    for row in re.split(r"<tr[^>]*>", html):
        cells = [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        # 欄位：代號、名稱、當月、上月、去年當月、上月比、去年同月比、當月累計、去年累計、前期比、備註
        if len(cells) >= 10 and re.fullmatch(r"\d{4,6}[A-Z]?", cells[0]):
            out[cells[0]] = (num(cells[2]), num(cells[4]))
    return url, out


def main():
    codes = set(company_codes())
    rp = DATA / "revenue.json"
    if not rp.exists():
        sys.exit("先跑一次 python3 scripts/fetch_official.py，讓它寫出最新月份")
    rev = json.loads(rp.read_text(encoding="utf-8"))
    latest = max(max(seq) for seq in rev.values() if seq)
    months = [month_shift(latest, -1), month_shift(latest, -2)]
    print(f"最新月份 {latest}，補 {months}")

    added = 0
    for ym in months:
        for market in ("sii", "otc"):
            for kind in (0, 1):
                url, rows = page(market, ym, kind)
                time.sleep(GAP)
                hit = 0
                for c, (cur, last) in rows.items():
                    if c not in codes:
                        continue
                    seq = rev.setdefault(c, {})
                    for when, v in ((ym, cur), (month_shift(ym, -12), last)):
                        if v is not None and when not in seq:
                            seq[when] = v
                            added += 1
                    hit += 1
                label = ("上市" if market == "sii" else "上櫃") + ("外國企業" if kind else "國內")
                print(f"  {ym} {label}：頁面 {len(rows)} 家，本專案 {hit} 家")

    for c in rev:
        rev[c] = dict(sorted(rev[c].items()))
    rp.write_text(json.dumps(rev, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"補入 {added} 個月份數字。接著重跑 fetch_official.py 計算年增")


if __name__ == "__main__":
    main()
