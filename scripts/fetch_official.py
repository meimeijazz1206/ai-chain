#!/usr/bin/env python3
"""用證交所與櫃買中心的官方開放資料抓收盤與基本面，寫進 data/quotes.json。

為什麼不用 Yahoo：網站要公開，Yahoo 的條款限制轉載；官方開放資料明文允許使用。
只用 Python 標準庫，所以 GitHub Actions 不需要安裝任何套件。

資料來源與取捨：
  收盤、漲跌　上市用證交所「每日收盤行情」（官網端點，收盤後當天就有；
              OpenAPI 版落後一天，會讓上市、上櫃日期對不上，所以不用），上櫃用櫃買 OpenAPI
  本益比等　　證交所 BWIBBU_d、櫃買 peratio_analysis
  毛利、營益　兩邊的「營益分析」，是最新一季財報，不是近四季
  市值　　　　已發行股數 × 收盤價
  預估本益比　官方不發布（那是分析師預估），固定留空
  半年位階、月線　官方沒有現成欄位，靠 data/history.json 每天累積收盤自己算。
              歷史存原始收盤，計算時用官方除權息表「還原權值」：事件之前的價格乘上
              參考價÷除權息前收盤價。不還原的話，分割或配股會被當成暴跌，
              例如緯穎 2026-09-02 由 7,800 元調整為約 2,615 元，位階會從 67% 誤算成 0%

日期一律以櫃買的最新交易日為基準，再去拿證交所同一天的資料，
確保兩個市場的收盤日永遠一致。

    python3 scripts/fetch_official.py
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (ai-chain research site)"}
HIST_KEEP = 130          # 保留約半年的交易日
POS_MIN_DAYS = 100       # 累積不到這麼多天就不算半年位階，免得用兩週資料冒充半年
MA_DAYS = 20


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as ex:          # 網路抖動就重試，三次都失敗才放棄
            if i == tries - 1:
                raise RuntimeError(f"抓不到 {url}：{ex}")
            time.sleep(3 * (i + 1))


def num(s):
    """官方資料的數字是字串，含千分位，停牌或無資料時是 -- 或 N/A。"""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v


def roc(d):
    """民國日期 1150924 轉成 2026-09-24。"""
    d = str(d).strip()
    return f"{int(d[:-4]) + 1911}-{d[-4:-2]}-{d[-2:]}"


def ymd_iso(s):
    """官方日期有好幾種寫法：115年06月11日、115/06/11、2026/06/11，一律轉成 2026-06-11。"""
    parts = [int(x) for x in "".join(ch if ch.isdigit() else " " for ch in str(s)).split()]
    y, m, d = parts[:3]
    return f"{y + 1911 if y < 1911 else y}-{m:02d}-{d:02d}"


def ex_right_factors(start, end, codes):
    """證交所與櫃買的除權息計算結果表 → {代號: [(除權息日, 還原係數), ...]}。
    還原係數 = 除權息參考價 ÷ 除權息前收盤價，配股與配息都還原。"""
    out = {}

    def add(code, when, before, ref):
        if code in codes and before and ref and before > 0:
            out.setdefault(code, []).append((when, ref / before))

    a, b = start.replace("-", ""), end.replace("-", "")
    tw = get(f"https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate={a}&endDate={b}&response=json")
    if tw.get("stat") == "OK":
        f = tw["fields"]
        for r in tw["data"]:
            add(r[f.index("股票代號")], ymd_iso(r[f.index("資料日期")]),
                num(r[f.index("除權息前收盤價")]), num(r[f.index("除權息參考價")]))
    s_, e_ = start.replace("-", "%2F"), end.replace("-", "%2F")
    otc = get(f"https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate={s_}&endDate={e_}&response=json")
    for t in otc.get("tables", []):
        f = [x.strip() for x in t.get("fields", [])]
        if "代號" in f and "除權息參考價" in f:
            for r in t["data"]:
                add(r[f.index("代號")], ymd_iso(r[f.index("除權息日期")]),
                    num(r[f.index("除權息前收盤價")]), num(r[f.index("除權息參考價")]))
    return out


def adjusted(seq, events):
    """把除權息日之前的收盤乘上還原係數，讓價格前後可以比較。"""
    res = []
    for d, c in seq:
        k = 1.0
        for when, fac in events:
            if when > d:
                k *= fac
        res.append(c * k)
    return res


def company_codes():
    chain = json.loads((DATA / "chain.json").read_text(encoding="utf-8"))
    return sorted({n["code"] for L in chain["layers"] for G in L["groups"] for n in G["nodes"]
                   if n.get("kind") == "company" and not n.get("ext")})


def main():
    codes = set(company_codes())

    # ── 上櫃：以它的最新交易日當基準 ─────────────────────────
    otc = get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
    day = max(roc(r["Date"]) for r in otc)
    ymd = day.replace("-", "")
    print(f"基準交易日 {day}")

    rows = {}   # code -> dict(close, change, market)
    for r in otc:
        c = r.get("SecuritiesCompanyCode")
        if c in codes and roc(r["Date"]) == day:
            rows[c] = {"close": num(r.get("Close")), "change": num(r.get("Change")), "mkt": ".TWO"}

    # ── 上市：拿證交所同一天 ─────────────────────────────────
    tw = get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json")
    if tw.get("stat") != "OK":
        sys.exit(f"證交所 {day} 還沒有收盤資料（{tw.get('stat')}），中止，不覆寫 quotes.json")
    table = next(t for t in tw["tables"] if "證券代號" in t.get("fields", []) and "收盤價" in t["fields"])
    f = table["fields"]
    ic, iclose, isign, idiff = f.index("證券代號"), f.index("收盤價"), f.index("漲跌(+/-)"), f.index("漲跌價差")
    for r in table["data"]:
        c = r[ic]
        if c in codes:
            sign = -1 if "-" in r[isign] else 1          # 漲跌符號包在 HTML 標籤裡
            diff = num(r[idiff])
            rows[c] = {"close": num(r[iclose]), "change": None if diff is None else sign * diff, "mkt": ".TW"}

    # ── 本益比、淨值比、殖利率 ───────────────────────────────
    val = {}
    b = get(f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={ymd}&selectType=ALL&response=json")
    if b.get("stat") == "OK":
        bf = b["fields"]
        for r in b["data"]:
            val[r[bf.index("證券代號")]] = (num(r[bf.index("本益比")]), num(r[bf.index("股價淨值比")]),
                                            num(r[bf.index("殖利率(%)")]))
    for r in get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"):
        val[r["SecuritiesCompanyCode"]] = (num(r.get("PriceEarningRatio")), num(r.get("PriceBookRatio")),
                                           num(r.get("YieldRatio")))

    # ── 最新一季毛利率、營益率 ───────────────────────────────
    margin = {}
    for r in get("https://openapi.twse.com.tw/v1/opendata/t187ap17_L"):
        margin[r["公司代號"]] = (num(r["毛利率(%)(營業毛利)/(營業收入)"]), num(r["營業利益率(%)(營業利益)/(營業收入)"]),
                                f"{int(r['年度']) + 1911}Q{r['季別']}")
    for r in get("https://www.tpex.org.tw/openapi/v1/mopsfin_187ap17_O"):
        margin[r["SecuritiesCompanyCode"]] = (num(r["毛利率"]), num(r["營業利益率"]),
                                             f"{int(r['Year']) + 1911}Q{r['季別']}")

    # ── 發行股數，用來算市值 ─────────────────────────────────
    shares = {}
    for r in get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"):
        shares[r["公司代號"]] = num(r.get("已發行普通股數或TDR原股發行股數"))
    for r in get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        shares[r["SecuritiesCompanyCode"]] = num(r.get("IssueShares"))

    # ── 歷史收盤：每天累積，用來算半年位階與月線 ─────────────
    hp = DATA / "history.json"
    hist = json.loads(hp.read_text(encoding="utf-8")) if hp.exists() else {}
    for c, r in rows.items():
        if r["close"] is None:
            continue
        seq = [p for p in hist.get(c, []) if p[0] != day]   # 同一天重跑就覆蓋，不重複
        seq.append([day, r["close"]])
        hist[c] = sorted(seq)[-HIST_KEEP:]

    starts = [seq[0][0] for seq in hist.values() if seq]
    events = ex_right_factors(min(starts), day, codes) if starts else {}
    if events:
        print(f"除權息事件 {sum(len(v) for v in events.values())} 筆，涵蓋 {len(events)} 檔")

    quotes, failed, gapped = {}, sorted(codes - set(rows)), []
    for c, r in sorted(rows.items()):
        close, chg = r["close"], r["change"]
        if close is None:
            failed.append(c)
            continue
        prev = close - chg if chg is not None else None
        closes = adjusted(hist.get(c, []), events.get(c, []))
        pos6m = above = None
        # 台股單日漲跌上限 10%，還原後還有超過 11% 的斷點，代表有官方表沒涵蓋的減資或面額變更。
        # 這時寧可留白，也不要顯示可能錯的位階
        if any(abs(closes[i] / closes[i - 1] - 1) > 0.11 for i in range(1, len(closes)) if closes[i - 1]):
            gapped.append(c)
            closes = []
        if len(closes) >= POS_MIN_DAYS:
            lo, hi = min(closes), max(closes)
            pos6m = round((close - lo) / (hi - lo) * 100, 1) if hi > lo else None
        if len(closes) >= MA_DAYS:
            above = close > sum(closes[-MA_DAYS:]) / MA_DAYS
        pe, pb, yd = val.get(c, (None, None, None))
        gm, om, period = margin.get(c, (None, None, None))
        # 最近一季財報之後股本大幅變動（分割、大量配股），官方的每股盈餘與每股股利
        # 還是舊股數，本益比與殖利率會錯好幾倍。例如緯穎 2026-09-02 一拆三後，
        # 官方本益比顯示 6.78 倍、殖利率 7.76%，實際約是三分之一與三倍。等下一季財報再恢復
        note = None
        if period:
            y, q = int(period[:4]), int(period[-1])
            qend = f"{y}-{q * 3:02d}-{[31, 30, 30, 31][q - 1]}"
            big = [w for w, fac in events.get(c, []) if w > qend and fac < 0.8]
            if big:
                pe = yd = None
                note = f"{big[-1]} 股本大幅變動（分割或配股）後，官方本益比與殖利率尚未換算，暫不顯示"
        sh = shares.get(c)
        quotes[c] = {
            "symbol": c + r["mkt"], "close": close,
            "changePct": round(chg / prev * 100, 2) if prev else None,
            "asOf": day, "currency": "TWD",
            "pos6m": pos6m, "aboveMa20": above,
            "perTtm": pe, "perFwd": None, "pbr": pb, "yieldPct": yd,
            "grossPct": gm, "opPct": om, "finPeriod": period,
            "mktcapB": round(sh * close / 1e8) if sh else None,
        }
        if note:
            quotes[c]["note"] = note

    if failed:
        print(f"⚠ {len(failed)} 檔沒有抓到：{failed}")
    if gapped:
        print(f"⚠ {len(gapped)} 檔還原後仍有異常斷點，半年位階與月線留白：{gapped}")
    if not quotes:
        sys.exit("一檔都沒抓到，中止，不覆寫 quotes.json")

    out = {"updatedAt": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
           "source": "臺灣證券交易所、證券櫃檯買賣中心開放資料",
           "quotes": quotes, "failed": failed}
    (DATA / "quotes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    hp.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"成功 {len(quotes)} 檔，失敗 {len(failed)} 檔，已寫入 quotes.json 與 history.json")


if __name__ == "__main__":
    main()
