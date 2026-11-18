#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/*.json 内联进 src/template.html，输出根目录 index.html。

产物是零依赖单文件：双击就能打开，不需要起服务器、不需要联网。
改了 data/ 下任意 json 后重跑一次即可：python3 src/build.py
"""

import datetime
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUT = BASE / "index.html"


def load(name, default):
    p = DATA / name
    if not p.is_file():
        if default is None:
            raise SystemExit(f"缺少数据文件: {p}")
        print(f"  · 未找到 {name}，用默认空数据")
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{name} 不是合法 JSON：{e}")


def main():
    payload = {
        "events": load("events.json", []),
        "earnings": load("earnings.json", {"updated": "", "items": []}),
        "finance": load("finance.json", {"updated": "", "items": []}),
        "companies": load("companies.json", {"companies": {}}),
        "macro": load("macro.json", {"templates": []}),
    }

    tpl = (BASE / "src" / "template.html").read_text(encoding="utf-8")
    for token in ("__DATA__", "__TODAY__"):
        if token not in tpl:
            raise SystemExit(f"模板缺少占位符 {token}")

    html = (
        tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
           .replace("__TODAY__", datetime.date.today().isoformat())
    )

    OUT.write_text(html, encoding="utf-8")

    n_ev = len(payload["events"])
    n_e = len(payload["earnings"].get("items", []))
    n_f = len(payload["finance"].get("items", []))
    n_c = len(payload["companies"].get("companies", {}))
    n_m = len(payload["macro"].get("templates", []))
    print(f"OK  {OUT}  ({len(html):,} 字节)")
    print(f"    普通事件 {n_ev} · 财经事件 {n_f} · 财报 {n_e} · 公司 {n_c} · 宏观模板 {n_m}")


if __name__ == "__main__":
    main()
