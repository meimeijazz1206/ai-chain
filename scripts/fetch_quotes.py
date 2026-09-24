#!/usr/bin/env python3
"""抓 chain.json 裡所有台股代號的行情，輸出 data/quotes.json 給前端讀。

用法：
    scripts/run.sh              # 用 tw-stock-screener 的 venv 跑
    python3 fetch_quotes.py --workers 8

設計重點：
- chain.json 只存四位數代號，不存 .TW/.TWO 後綴。後綴由本腳本自動試出來
  並快取在 data/suffix.json，避免手動維護上市／上櫃分類出錯。
- 抓不到的個股不會讓整批失敗，會列在 quotes.json 的 failed 裡。
- 資料為 Yahoo 延遲報價，僅供研究。
"""
import argparse
import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    sys.exit("找不到 yfinance。請用 tw-stock-screener 的 venv 執行，見 scripts/run.sh")

ROOT = Path(__file__).resolve().parent.parent
CHAIN = ROOT / "data" / "chain.json"
OUT = ROOT / "data" / "quotes.json"
SUFFIX_CACHE = ROOT / "data" / "suffix.json"
TPE = timezone(timedelta(hours=8))


def collect_codes(chain):
    """走訪所有層與群組，取出非外部錨點的台股代號。"""
    codes = []
    for layer in chain["layers"]:
        for group in layer["groups"]:
            for node in group["nodes"]:
                if not node.get("ext"):
                    codes.append(node["code"])
    return sorted(set(codes))


def load_suffix_cache():
    if SUFFIX_CACHE.exists():
        try:
            return json.loads(SUFFIX_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def pick(info, *keys):
    for k in keys:
        v = info.get(k)
        if v is not None:
            return v
    return None


def fetch_one(code, cached_suffix):
    """回傳 (code, payload or None)。先試快取後綴，失敗再試另一個。"""
    order = [cached_suffix] if cached_suffix else []
    order += [s for s in (".TW", ".TWO") if s not in order]

    for suffix in order:
        symbol = f"{code}{suffix}"
        try:
            tk = yf.Ticker(symbol)
            hist = tk.history(period="6mo", auto_adjust=False)
            if hist.empty:
                continue

            # Yahoo 會補一列當日尚未成交的空白資料，Close 是 NaN。
            # 不濾掉的話整批行情都會變 NaN，而且不會報錯。
            window = hist["Close"].dropna()
            if len(window) < 2:
                continue

            close = float(window.iloc[-1])
            prev = float(window.iloc[-2])
            change_pct = (close / prev - 1) * 100 if prev else None
            lo, hi = float(window.min()), float(window.max())
            pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50.0

            ma20 = float(window.tail(20).mean()) if len(window) >= 20 else None

            info = {}
            try:
                info = tk.info or {}
            except Exception:
                pass

            def num(key, digits=2, scale=1.0):
                v = info.get(key)
                return round(v * scale, digits) if isinstance(v, (int, float)) else None

            mcap = pick(info, "marketCap")
            return code, {
                "symbol": symbol,
                "close": round(close, 2),
                "changePct": round(change_pct, 2) if change_pct is not None else None,
                # 這根 K 棒的日期。收盤價是哪一天的，前端要講清楚，不能只寫「更新時間」
                "asOf": str(window.index[-1].date()),
                "currency": info.get("currency") or "TWD",
                "pos6m": round(pos, 1),
                "aboveMa20": (close > ma20) if ma20 else None,
                # trailingPE 是近四季，forwardPE 是市場預估，兩者口徑不同必須分開存
                "perTtm": num("trailingPE", 1),
                "perFwd": num("forwardPE", 1),
                "pbr": num("priceToBook"),
                "yieldPct": num("dividendYield"),
                "grossPct": num("grossMargins", 1, 100),
                "opPct": num("operatingMargins", 1, 100),
                "mktcapB": round(mcap / 1e8, 0) if isinstance(mcap, (int, float)) else None,
            }
        except Exception:
            continue
    return code, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    chain = json.loads(CHAIN.read_text(encoding="utf-8"))
    codes = collect_codes(chain)
    cache = load_suffix_cache()
    print(f"共 {len(codes)} 檔待抓，workers={args.workers}", file=sys.stderr)

    quotes, failed, new_cache = {}, [], {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = pool.map(lambda c: fetch_one(c, cache.get(c)), codes)
        for code, payload in results:
            if payload:
                quotes[code] = payload
                new_cache[code] = payload["symbol"][len(code):]
            else:
                failed.append(code)

    # 收盤價是 NaN 代表抓到空白列，寧可整批不寫也不要寫壞資料
    nan = [c for c, d in quotes.items() if d["close"] != d["close"]]
    if nan:
        sys.exit(f"中止：{len(nan)} 檔收盤價為 NaN，未覆寫 quotes.json。{nan[:10]}")

    SUFFIX_CACHE.write_text(
        json.dumps({**cache, **new_cache}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    OUT.write_text(
        json.dumps(
            {
                "updatedAt": datetime.now(TPE).strftime("%Y-%m-%d %H:%M"),
                "source": "Yahoo Finance 延遲報價，僅供研究",
                "quotes": quotes,
                "failed": failed,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"成功 {len(quotes)} 檔，失敗 {len(failed)} 檔 {failed}", file=sys.stderr)
    print(f"已寫入 {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
