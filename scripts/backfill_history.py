#!/usr/bin/env python3
"""一次性回補過去約半年的官方收盤到 data/history.json。

fetch_official.py 每天只累積一天，要等將近五個月才算得出半年位階。
這支從官方歷史收盤一次補齊，之後就交給每日流程接手。

證交所對密集請求會封 IP，所以每次請求間隔 3 秒，跳過週末，
已經有的日期不重抓，中斷了可以直接重跑接續。

    python3 scripts/backfill_history.py
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_official import DATA, HIST_KEEP, company_codes, get, num   # noqa: E402

TARGET_DAYS = 125
GAP = 3.0


def main():
    codes = set(company_codes())
    hp = DATA / "history.json"
    hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    have = {p[0] for seq in hist.values() for p in seq}
    newest = max(have) if have else date.today().isoformat()

    d = date.fromisoformat(newest)
    got, tried = len(have), 0
    while got < TARGET_DAYS and tried < 200:
        d -= timedelta(days=1)
        if d.weekday() >= 5:            # 週末不開市，不必問
            continue
        iso = d.isoformat()
        if iso in have:
            continue
        tried += 1
        tw = get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json")
        time.sleep(GAP)
        if tw.get("stat") != "OK":      # 國定假日
            print(f"  {iso} 休市")
            continue
        t = next(t for t in tw["tables"] if "證券代號" in t.get("fields", []) and "收盤價" in t["fields"])
        ic, iclose = t["fields"].index("證券代號"), t["fields"].index("收盤價")
        day = {r[ic]: num(r[iclose]) for r in t["data"] if r[ic] in codes}

        otc = get(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y}%2F{d:%m}%2F{d:%d}&type=EW&response=json")
        time.sleep(GAP / 2)
        for tb in otc.get("tables", []):
            f = [x.strip() for x in tb.get("fields", [])]
            if "代號" in f and "收盤" in f:
                ic2, ic3 = f.index("代號"), f.index("收盤")
                day.update({r[ic2]: num(r[ic3]) for r in tb["data"] if r and r[ic2] in codes})
                break

        n = 0
        for c, v in day.items():
            if v is not None:
                hist.setdefault(c, []).append([iso, v]); n += 1
        got += 1
        print(f"  {iso} 補入 {n} 檔（已有 {got} 個交易日）", flush=True)

    for c in hist:
        seen = {}
        for dd, v in hist[c]:
            seen[dd] = v
        hist[c] = sorted(seen.items())[-HIST_KEEP:]
        hist[c] = [list(p) for p in hist[c]]
    hp.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    lens = sorted(len(v) for v in hist.values())
    print(f"完成：{len(hist)} 檔，每檔交易日數 最少 {lens[0]}、中位數 {lens[len(lens)//2]}、最多 {lens[-1]}")


if __name__ == "__main__":
    main()
