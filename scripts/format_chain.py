#!/usr/bin/env python3
"""chain.json 的格式化工具。

用 json.dump 會把每個個股物件攤成五六行，檔案長度暴增而且難讀。
這個 dumper 讓「夠短且沒有巢狀容器」的物件維持單行，個股與關聯線因此各佔一行，
其餘照常縮排。任何用程式改過 chain.json 之後都用它寫回，格式才不會走樣。

單獨執行等同原地重新格式化：
    python3 scripts/format_chain.py
"""
import json
import sys
from pathlib import Path

CHAIN = Path(__file__).resolve().parent.parent / "data" / "chain.json"
INLINE_MAX = 260


def _inlineable(obj):
    """純量、純量清單，或全部由前述組成的物件，才允許壓成一行。"""
    if isinstance(obj, dict):
        return all(_inlineable(v) for v in obj.values())
    if isinstance(obj, list):
        return all(not isinstance(v, (dict, list)) for v in obj)
    return True


def dumps(obj, indent=0):
    pad, pad2 = " " * indent, " " * (indent + 2)

    if _inlineable(obj):
        flat = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
        if len(flat) + indent <= INLINE_MAX:
            return flat

    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = [f'{pad2}{json.dumps(k, ensure_ascii=False)}: {dumps(v, indent + 2)}'
                 for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"

    if isinstance(obj, list):
        if not obj:
            return "[]"
        items = [f"{pad2}{dumps(v, indent + 2)}" for v in obj]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"

    return json.dumps(obj, ensure_ascii=False)


def write(data, path=CHAIN):
    text = dumps(data) + "\n"
    json.loads(text)  # 寫回前確認仍是合法 JSON
    path.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    data = json.loads(CHAIN.read_text(encoding="utf-8"))
    write(data)
    print(f"已重新格式化 {CHAIN}", file=sys.stderr)
