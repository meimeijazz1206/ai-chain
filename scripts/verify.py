#!/usr/bin/env python3
"""上線前的自我檢查。

分兩種問題：
  ERROR  資料壞了或報價不可信，絕對不能上線
  WARN   需要人看一眼，但不擋上線（例如某檔單日暴漲、某條關聯線很久沒複查）

離開碼 0 代表可以發布，1 代表有 ERROR 必須先修。

    python3 scripts/verify.py            # 完整檢查
    python3 scripts/verify.py --rotate 3 # 另外抽 3 條最久沒複查的線，列出來給人重查
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date.today()
STALE_DAYS = 90          # 關聯線超過這個天數沒複查就列入待辦
QUOTE_STALE_DAYS = 5     # 收盤日距今超過這麼多天就是抓取出問題
MOVE_ALERT = 15.0        # 單日漲跌超過這個百分比，值得人看一眼

errors, warns = [], []
E = errors.append
W = warns.append


def load(name):
    p = ROOT / "data" / name
    if not p.exists():
        E(f"{name} 不存在")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        E(f"{name} 不是合法 JSON：{ex}")
        return None


def check_chain(chain):
    nodes, dup = {}, []
    for L in chain["layers"]:
        for G in L["groups"]:
            for n in G["nodes"]:
                if n["code"] in nodes:
                    dup.append(n["code"])
                nodes[n["code"]] = (n, L, G)
    if dup:
        E(f"代號重複：{sorted(set(dup))}")

    layer_ids = {L["id"] for L in chain["layers"]}
    for code, (n, L, G) in nodes.items():
        if n.get("kind") == "company":
            for k in ("purity", "bottleneck", "subst"):
                v = n.get(k)
                if not isinstance(v, int) or not 1 <= v <= 5:
                    E(f"{n['name']}({code}) 的 {k} 不是 1-5：{v!r}")
            if not n.get("role"):
                W(f"{n['name']}({code}) 沒有寫定位")
        for lid in n.get("cross", []):
            if lid not in layer_ids:
                E(f"{n['name']}({code}) 的 cross 指向不存在的層 {lid}")
        if n.get("cross") and L["id"] not in n["cross"]:
            E(f"{n['name']}({code}) 的 cross 沒有包含自己所在的 {L['id']}")

    types = set(chain["edgeTypes"])
    confs = set(chain["confidence"])
    for e in chain["edges"]:
        for side in ("from", "to"):
            if e[side] not in nodes:
                E(f"關聯線指向不存在的節點：{e['from']} → {e['to']}（{e[side]}）")
        if e["type"] not in types:
            E(f"未知的線型 {e['type']}：{e['from']} → {e['to']}")
        if e.get("conf") and e["conf"] not in confs:
            E(f"未知的可信度 {e['conf']}：{e['from']} → {e['to']}")

    for t in chain.get("themes", []):
        miss = [c for c in t["codes"] if c not in nodes]
        if miss:
            E(f"主題「{t['name']}」指向不存在的代號：{miss}")
    return nodes


def check_quotes(nodes, quotes):
    qs = quotes.get("quotes", {})
    companies = [c for c, (n, _, _) in nodes.items()
                 if n.get("kind") == "company" and not n.get("ext")]
    missing = [c for c in companies if c not in qs]
    if missing:
        E(f"{len(missing)} 檔台股沒有報價：{missing[:12]}")

    nan = [c for c, v in qs.items() if v.get("close") != v.get("close")]
    if nan:
        E(f"{len(nan)} 檔收盤價是 NaN，抓取出問題：{nan[:12]}")

    days = sorted({v["asOf"] for v in qs.values() if v.get("asOf")})
    if not days:
        E("報價裡沒有任何收盤日")
    else:
        gap = (TODAY - dt.date.fromisoformat(days[-1])).days
        if gap > QUOTE_STALE_DAYS:
            E(f"最新收盤日是 {days[-1]}，距今 {gap} 天，報價沒有更新成功")
        elif gap > 2:
            W(f"最新收盤日 {days[-1]}，距今 {gap} 天（遇到連假可能正常）")

    big = [(c, v["changePct"]) for c, v in qs.items()
           if isinstance(v.get("changePct"), (int, float)) and abs(v["changePct"]) >= MOVE_ALERT]
    for c, pct in sorted(big, key=lambda x: -abs(x[1])):
        name = nodes[c][0]["name"] if c in nodes else c
        W(f"{name}({c}) 單日 {pct:+.1f}%，確認不是分割或資料錯誤")


def check_products(nodes):
    p = ROOT / "data" / "products.json"
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    for prod in data["products"]:
        paths = {x["id"] for x in prod["paths"]}
        for st in prod["stages"]:
            for pt in st["parts"]:
                for c in pt.get("codes", []):
                    if c not in nodes:
                        E(f"產品「{prod['name']}」的部件「{pt['name']}」指向不存在的代號 {c}")
                if pt.get("only") and pt["only"] not in paths:
                    E(f"部件「{pt['name']}」的 only 指向不存在的路線 {pt['only']}")


def check_updates(nodes):
    p = ROOT / "data" / "updates.json"
    if not p.exists():
        return
    try:
        items = json.loads(p.read_text(encoding="utf-8")).get("items", [])
    except json.JSONDecodeError as ex:
        E(f"updates.json 不是合法 JSON：{ex}")
        return
    for i, it in enumerate(items, 1):
        where = f"跑馬燈第 {i} 則"
        for k in ("date", "tag", "text", "source", "url"):
            if not it.get(k):
                E(f"{where} 缺少 {k}")
        if it.get("tag") not in ("new", "fix", "news"):
            E(f"{where} 的 tag 只能是 new／fix／news：{it.get('tag')!r}")
        if not str(it.get("url", "")).startswith("https://"):
            E(f"{where} 的來源不是 https 連結：{it.get('url')!r}")
        try:
            age = (TODAY - dt.date.fromisoformat(it.get("date", ""))).days
            if age > 60:
                W(f"{where}「{it.get('text','')[:16]}」已經 {age} 天，考慮撤下，跑馬燈應該只放最新的")
        except ValueError:
            E(f"{where} 的日期格式錯誤：{it.get('date')!r}")
        for c in it.get("codes", []):
            if c not in nodes:
                E(f"{where} 指向不存在的代號 {c}")


def aging(chain, rotate):
    rows = []
    for e in chain["edges"]:
        if e.get("conf") == "common":
            continue           # 產業共識層級不需要逐條追日期
        d = e.get("checked")
        age = (TODAY - dt.date.fromisoformat(d)).days if d else None
        if age is None or age > STALE_DAYS:
            rows.append((age if age is not None else 10**6, e))
    rows.sort(key=lambda x: -x[0])
    never = sum(1 for a, _ in rows if a == 10**6)
    if rows:
        W(f"{len(rows)} 條關聯線需要複查（其中 {never} 條從未查證過）")
    if rotate and rows:
        print(f"\n── 今天該複查的 {min(rotate,len(rows))} 條 ──")
        for age, e in rows[:rotate]:
            when = "從未查證" if age == 10**6 else f"{age} 天前查過"
            print(f"  [{e.get('conf','common')}] {e['from']} → {e['to']}　{when}")
            print(f"     {e.get('note','')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", type=int, default=0)
    a = ap.parse_args()

    chain = load("chain.json")
    quotes = load("quotes.json")
    if chain and quotes:
        nodes = check_chain(chain)
        check_quotes(nodes, quotes)
        check_products(nodes)
        check_updates(nodes)
        aging(chain, a.rotate)

    print(f"\n檢查於 {TODAY}")
    for w in warns:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    if errors:
        print(f"\n✗ {len(errors)} 個錯誤，不可上線")
        return 1
    print(f"\n✓ 通過，可以上線（{len(warns)} 個提醒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
