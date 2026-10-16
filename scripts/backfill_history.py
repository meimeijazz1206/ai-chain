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
from fetch_official import DATA, HIST_KEEP, company_codes, fetch_day   # noqa: E402

TARGET_DAYS = 125
GAP = 3.0


def save(hp, hist):
    for c in hist:
        seen = {}
        for dd, v in hist[c]:
            seen[dd] = v
        hist[c] = [list(p) for p in sorted(seen.items())[-HIST_KEEP:]]
    hp.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    codes = set(company_codes())
    hp = DATA / "history.json"
    hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    have = {p[0] for seq in hist.values() for p in seq}
    newest = max(have) if have else date.today().isoformat()

    # 第一段：整體交易日數不足就往前補
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
        day = fetch_day(d, codes, GAP)
        if day is None:
            print(f"  {iso} 休市")
            continue
        n = 0
        for c, v in day.items():
            if v is not None:
                hist.setdefault(c, []).append([iso, v]); n += 1
        got += 1
        have.add(iso)
        print(f"  {iso} 補入 {n} 檔（已有 {got} 個交易日）", flush=True)
    save(hp, hist)

    # 第二段：新加入的公司。日期早就被其他公司抓過，第一段會整個跳過，
    # 所以要找出「整體有、但這幾檔沒有」的日子，逐日補齊
    days = sorted(have)[-TARGET_DAYS:]
    owned = {c: {p[0] for p in hist.get(c, [])} for c in codes}
    short = sorted(c for c in codes if len(owned[c] & set(days)) < len(days) - 5)
    if short:
        todo = [dd for dd in days if any(dd not in owned[c] for c in short)]
        print(f"新公司 {len(short)} 檔要補 {len(todo)} 個交易日（約 {len(todo) * GAP * 1.5 / 60:.0f} 分鐘）：{short}", flush=True)
        for i, iso in enumerate(todo, 1):
            day = fetch_day(date.fromisoformat(iso), set(short), GAP) or {}
            for c in short:
                if day.get(c) is not None and iso not in owned[c]:
                    hist.setdefault(c, []).append([iso, day[c]])
                    owned[c].add(iso)
            if i % 10 == 0:
                save(hp, hist)
                print(f"  已補 {i}/{len(todo)} 天", flush=True)
        save(hp, hist)

    lens = sorted(len(v) for v in hist.values())
    print(f"完成：{len(hist)} 檔，每檔交易日數 最少 {lens[0]}、中位數 {lens[len(lens)//2]}、最多 {lens[-1]}")


if __name__ == "__main__":
    main()
