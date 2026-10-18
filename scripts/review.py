#!/usr/bin/env python3
"""每日查證的單一入口，給 Claude 排程用。

排程只需要授權這一支指令（加上網路搜尋），不必為 git、檢查、改檔各按一次允許。
抓報價與發布交給 GitHub 雲端，這支只負責「說法對不對」。

輪替的對象有三種，不確定性越高的越常查：
    關聯線（產業推測、媒體報導、已揭露）  每 60 天
    主題說明                            每 45 天
    公司與技術節點的描述                  每 120 天
    關聯線（產業共識）                    每 180 天
共識級的線也要輪替：2026-10 的總體檢發現「OpenAI 晶片由台積電代工」標成共識，其實只是媒體報導。
從沒查證過的項目會依優先順序分批排進未來的日子，不會一次擠在最前面，
也不會讓已經查過、但快到期的推測級關聯線一直排不到。

    python3 scripts/review.py start [N]                        同步並列出今天要查的 N 項（預設 5）
    python3 scripts/review.py ok ID [ID ...]                    查證後仍然成立，蓋上今天日期
    python3 scripts/review.py fix ID --text "..." --ticker "改了什麼" --source "..." --url https://... --expect "關鍵字,關鍵字" [--codes 2330] [--conf C] [--type T]
    python3 scripts/review.py ticker --tag news --text "..." --source "..." --url https://... --expect "關鍵字,關鍵字" [--codes 2330,3711]
    python3 scripts/review.py finish                           再檢查一次，有變更才提交並推上 GitHub

ID 的寫法：關聯線 E:來源>對象:型態，主題 T:主題代號，節點 N:代號。start 會直接印出來。

改說法或加跑馬燈之前，程式會先打開來源頁核對：AI 生成的頁面一律退回；--expect 至少兩個關鍵字
（新說法裡的數字、日期、公司或產品名），而且都要真的出現在那一頁上。
2026-10-16 第一次排程就錯了兩次：引用 TechNews 的 AI 問答頁，以及把別篇報導的時程掛在這篇底下。
"""
import argparse
import html
import json
import re
import subprocess
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import format_chain   # noqa: E402
from verify import AI_PAGE_URL   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UPDATES = ROOT / "data" / "updates.json"
AI_PAGE_TEXT = ("內容由 AI 協助生成", "Powered by TechNews Smart AI")   # 頁面上的 AI 生成聲明
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
TODAY = datetime.now(timezone(timedelta(hours=8))).date()
BACKLOG_START = date(2026, 10, 16)       # 開始輪替的日子，從沒查過的項目從這天起分批排程
BACKLOG_PER_DAY = 3                      # 以日曆天計，約等於每個交易日四項
INTERVAL = {"edge": 60, "theme": 45, "node": 120, "common": 180}
PRIORITY = {"theme": 0, "edge": 1, "node": 2, "common": 3}
TYPES = {"supply", "alliance", "substitute", "crowding", "equity", "group"}
CONFS = {"disclosed", "reported", "inferred", "common"}


def run(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def items(d):
    """把資料攤平成可輪替的項目：(ID, 種類, 物件, 說明欄位名, 顯示用標題)。"""
    names = {n["code"]: n["name"] for L in d["layers"] for G in L["groups"] for n in G["nodes"]}
    out = []
    for t in d.get("themes", []):
        out.append((f"T:{t['id']}", "theme", t, "note", f"主題「{t['name']}」"))
    for e in d["edges"]:
        kind = "common" if e.get("conf") == "common" else "edge"
        title = f"{names.get(e['from'], e['from'])} → {names.get(e['to'], e['to'])}（{e['type']}）"
        out.append((f"E:{e['from']}>{e['to']}:{e['type']}", kind, e, "note", title))
    for L in d["layers"]:
        for G in L["groups"]:
            for n in G["nodes"]:
                if n.get("role"):
                    label = n["name"] + ("" if n.get("ext") else f"（{n['code']}）")
                    out.append((f"N:{n['code']}", "node", n, "role", f"{label}　{L['id']} {G['name']}"))
    return out


def schedule(d):
    """每一項的到期日。查過的：上次查證日加間隔。沒查過的：依優先順序分批排進未來。"""
    rows, backlog = [], []
    for it in items(d):
        iid, kind, obj = it[0], it[1], it[2]
        if obj.get("checked"):
            due = date.fromisoformat(obj["checked"]) + timedelta(days=INTERVAL[kind])
            rows.append((due, PRIORITY[kind], iid, it))
        else:
            backlog.append((PRIORITY[kind], iid, it))
    backlog.sort()
    for k, (pri, iid, it) in enumerate(backlog):
        rows.append((BACKLOG_START + timedelta(days=k // BACKLOG_PER_DAY), pri, iid, it))
    rows.sort()
    return rows


def find(d, iid):
    for it in items(d):
        if it[0] == iid:
            return it
    sys.exit(f"找不到 {iid}。ID 要照 start 印出來的寫法")


def cmd_start(n):
    r = run("git", "pull", "-q", "--rebase", "origin", "main")
    if r.returncode != 0:
        sys.exit(f"git pull 失敗，先不要動資料：\n{r.stderr.strip()}")
    v = run(sys.executable, str(ROOT / "scripts" / "verify.py"))
    print(v.stdout.strip())
    if v.returncode != 0:
        sys.exit("自我檢查沒過，今天不查證，先把錯誤回報給 Mei")
    d = json.loads(format_chain.CHAIN.read_text(encoding="utf-8"))
    rows = schedule(d)
    due = [r for r in rows if r[0] <= TODAY]
    pick = rows[:n]          # 永遠取最早到期的 n 項，已到期的自然排最前面
    label = {"theme": "主題說明", "edge": "關聯線", "common": "關聯線（產業共識）", "node": "節點描述"}
    print(f"\n── 今天要查證的 {len(pick)} 項（已到期 {len(due)} 項）──")
    for due_day, _, iid, (_, kind, obj, field, title) in pick:
        last = obj.get("checked") or "從未查證"
        conf = f"　可信度 {obj['conf']}" if "conf" in obj else ""
        print(f"\n[{label[kind]}] {title}")
        print(f"  ID：{iid}")
        print(f"  上次查證：{last}{conf}")
        print(f"  目前寫的：{obj.get(field, '')}")


def stamp(obj):
    obj["checked"] = TODAY.isoformat()


def cmd_ok(ids):
    d = json.loads(format_chain.CHAIN.read_text(encoding="utf-8"))
    for iid in ids:
        it = find(d, iid)
        stamp(it[2])
        print(f"✓ {it[4]}：維持，已蓋上 {TODAY}")
    format_chain.write(d)


def flat(s):
    """去掉空白、全形轉半形、英文轉小寫，比對關鍵字時不受排版影響。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()


def check_source(url, expect):
    """打開來源頁核對，任何一關沒過就停下來，資料一個字都不動。"""
    url = url.strip()
    if not url.startswith("https://"):
        sys.exit("來源必須是 https 連結")
    if any(p in url for p in AI_PAGE_URL):
        sys.exit("這是 AI 生成的頁面，只能當線索，不能當來源。打開它引用的原始報導，改用那一篇")
    keys = [k.strip() for k in (expect or "").split(",") if k.strip()]
    if len(keys) < 2:
        sys.exit("--expect 至少給兩個關鍵字（新說法裡的數字、日期、公司或產品名），用來確認來源頁真的寫著")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read(4_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception as ex:
        sys.exit(f"來源打不開（{ex}），沒辦法確認上面寫了什麼。換一個打得開的來源，或今天先不改")
    if "html" not in ctype and "text" not in ctype:
        sys.exit(f"來源不是網頁（{ctype or '格式不明'}），程式讀不到內文。改用報導或新聞稿的網頁版")
    text = flat(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw))))
    if any(flat(m) in text for m in AI_PAGE_TEXT):
        sys.exit("這一頁註明是 AI 生成的內容，只能當線索，不能當來源")
    missing = [k for k in keys if flat(k) not in text]
    if missing:
        sys.exit(f"來源頁上找不到 {missing}。說法要以這一頁實際寫的為準；資料其實出自別篇，就引用那一篇")
    print(f"✓ 來源已打開核對，頁面上寫著：{'、'.join(keys)}")


def parse_codes(d, codes):
    known = {n["code"] for L in d["layers"] for G in L["groups"] for n in G["nodes"]}
    codes = [c.strip() for c in (codes or "").split(",") if c.strip()]
    bad = [c for c in codes if c not in known]
    if bad:
        sys.exit(f"這些代號不在資料裡：{bad}")
    return codes


def add_ticker(tag, text, source, url, codes):
    u = json.loads(UPDATES.read_text(encoding="utf-8"))
    u["items"].insert(0, {"date": TODAY.isoformat(), "tag": tag, "text": text.strip(),
                          "source": source.strip(), "url": url.strip(), "codes": codes})
    UPDATES.write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 跑馬燈新增一則：{text.strip()}")


def cmd_fix(iid, text, conf, typ, ticker, source, url, expect, codes):
    """改說法一定附上查證過的來源：先核對來源，再一次寫進資料與跑馬燈，不會只改一半。"""
    d = json.loads(format_chain.CHAIN.read_text(encoding="utf-8"))
    _, kind, obj, field, title = find(d, iid)
    if conf and (conf not in CONFS or kind not in ("edge", "common")):
        sys.exit(f"可信度只能用在關聯線，而且只有 {sorted(CONFS)}")
    if typ and (typ not in TYPES or kind not in ("edge", "common")):
        sys.exit(f"型態只能用在關聯線，而且只有 {sorted(TYPES)}")
    codes = parse_codes(d, codes)
    check_source(url, expect)
    if text:
        obj[field] = text.strip()
    if conf:
        obj["conf"] = conf
    if typ:
        obj["type"] = typ
    stamp(obj)
    format_chain.write(d)
    add_ticker("fix", ticker, source, url, codes)
    print(f"✎ {title}：已修正並蓋上 {TODAY}")


def cmd_ticker(tag, text, source, url, codes, expect):
    if tag not in ("new", "fix", "news"):
        sys.exit("tag 只能是 new、fix、news")
    codes = parse_codes(json.loads(format_chain.CHAIN.read_text(encoding="utf-8")), codes)
    check_source(url, expect)
    add_ticker(tag, text, source, url, codes)


def summarize():
    """跟上一次提交比，算出今天複查與修正了哪些項目。"""
    r = run("git", "show", "HEAD:data/chain.json")
    if r.returncode != 0:
        return [], []
    old = {it[0]: it[2] for it in items(json.loads(r.stdout))}
    oks, fixes = [], []
    for iid, _, obj, field, title in items(json.loads(format_chain.CHAIN.read_text(encoding="utf-8"))):
        before = old.get(iid)
        if before is None:
            continue
        changed = any(before.get(k) != obj.get(k) for k in (field, "conf", "type"))
        if changed:
            fixes.append(title)
        elif before.get("checked") != obj.get("checked"):
            oks.append(title)
    return oks, fixes


def cmd_finish():
    v = run(sys.executable, str(ROOT / "scripts" / "verify.py"))
    if v.returncode != 0:
        print(v.stdout.strip())
        sys.exit("自我檢查沒過，不提交。把錯誤回報給 Mei，不要硬推")
    run("git", "add", "data/chain.json", "data/updates.json")
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        print("沒有任何變更，不提交")
        return
    oks, fixes = summarize()
    body = "\n".join([f"- 修正：{t}" for t in fixes] + [f"- 維持：{t}" for t in oks])
    msg = (f"每日查證 {TODAY}：複查 {len(oks) + len(fixes)} 項，修正 {len(fixes)} 項\n\n{body}\n\n"
           "Co-Authored-By: Claude <noreply@anthropic.com>")
    c = run("git", "commit", "-q", "-m", msg)
    if c.returncode != 0:
        sys.exit(f"提交失敗：{c.stderr.strip()}")
    p = run("git", "pull", "-q", "--rebase", "origin", "main")
    if p.returncode != 0:
        sys.exit(f"同步雲端失敗，提交留在本機沒有推上去：{p.stderr.strip()}")
    push = run("git", "push", "-q", "origin", "main")
    if push.returncode != 0:
        sys.exit(f"推送失敗，提交留在本機：{push.stderr.strip()}")
    print(f"已推上 GitHub：複查 {len(oks) + len(fixes)} 項，修正 {len(fixes)} 項。網站會在幾分鐘內自動更新")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("n", nargs="?", type=int, default=5)
    o = sub.add_parser("ok"); o.add_argument("ids", nargs="+")
    f = sub.add_parser("fix"); f.add_argument("id"); f.add_argument("--text")
    f.add_argument("--conf"); f.add_argument("--type")
    f.add_argument("--ticker", required=True); f.add_argument("--source", required=True)
    f.add_argument("--url", required=True); f.add_argument("--expect", required=True); f.add_argument("--codes")
    t = sub.add_parser("ticker"); t.add_argument("--tag", required=True); t.add_argument("--text", required=True)
    t.add_argument("--source", required=True); t.add_argument("--url", required=True)
    t.add_argument("--expect", required=True); t.add_argument("--codes")
    sub.add_parser("finish")
    a = ap.parse_args()
    if a.cmd == "start":
        cmd_start(a.n)
    elif a.cmd == "ok":
        cmd_ok(a.ids)
    elif a.cmd == "fix":
        if not (a.text or a.conf or a.type):
            sys.exit("fix 至少要給 --text、--conf、--type 其中一個")
        cmd_fix(a.id, a.text, a.conf, a.type, a.ticker, a.source, a.url, a.expect, a.codes)
    elif a.cmd == "ticker":
        cmd_ticker(a.tag, a.text, a.source, a.url, a.codes, a.expect)
    else:
        cmd_finish()


if __name__ == "__main__":
    main()
