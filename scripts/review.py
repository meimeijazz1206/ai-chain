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
    python3 scripts/review.py fix ID --text "..." [--conf C] [--type T]   說法要改
    python3 scripts/review.py ticker --tag fix --text "..." --source "..." --url https://... [--codes 2330,3711]
    python3 scripts/review.py finish                           再檢查一次，有變更才提交並推上 GitHub

ID 的寫法：關聯線 E:來源>對象:型態，主題 T:主題代號，節點 N:代號。start 會直接印出來。
"""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import format_chain   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UPDATES = ROOT / "data" / "updates.json"
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


def cmd_fix(iid, text, conf, typ):
    d = json.loads(format_chain.CHAIN.read_text(encoding="utf-8"))
    _, kind, obj, field, title = find(d, iid)
    if text:
        obj[field] = text.strip()
    if conf:
        if conf not in CONFS or kind not in ("edge", "common"):
            sys.exit(f"可信度只能用在關聯線，而且只有 {sorted(CONFS)}")
        obj["conf"] = conf
    if typ:
        if typ not in TYPES or kind not in ("edge", "common"):
            sys.exit(f"型態只能用在關聯線，而且只有 {sorted(TYPES)}")
        obj["type"] = typ
    stamp(obj)
    format_chain.write(d)
    print(f"✎ {title}：已修正並蓋上 {TODAY}")


def cmd_ticker(tag, text, source, url, codes):
    if tag not in ("new", "fix", "news"):
        sys.exit("tag 只能是 new、fix、news")
    if not url.startswith("https://"):
        sys.exit("來源必須是 https 連結")
    d = json.loads(format_chain.CHAIN.read_text(encoding="utf-8"))
    known = {n["code"] for L in d["layers"] for G in L["groups"] for n in G["nodes"]}
    codes = [c.strip() for c in (codes or "").split(",") if c.strip()]
    bad = [c for c in codes if c not in known]
    if bad:
        sys.exit(f"這些代號不在資料裡：{bad}")
    u = json.loads(UPDATES.read_text(encoding="utf-8"))
    u["items"].insert(0, {"date": TODAY.isoformat(), "tag": tag, "text": text.strip(),
                          "source": source.strip(), "url": url.strip(), "codes": codes})
    UPDATES.write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 跑馬燈新增一則：{text.strip()}")


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
    t = sub.add_parser("ticker"); t.add_argument("--tag", required=True); t.add_argument("--text", required=True)
    t.add_argument("--source", required=True); t.add_argument("--url", required=True); t.add_argument("--codes")
    sub.add_parser("finish")
    a = ap.parse_args()
    if a.cmd == "start":
        cmd_start(a.n)
    elif a.cmd == "ok":
        cmd_ok(a.ids)
    elif a.cmd == "fix":
        if not (a.text or a.conf or a.type):
            sys.exit("fix 至少要給 --text、--conf、--type 其中一個")
        cmd_fix(a.id, a.text, a.conf, a.type)
    elif a.cmd == "ticker":
        cmd_ticker(a.tag, a.text, a.source, a.url, a.codes)
    else:
        cmd_finish()


if __name__ == "__main__":
    main()
