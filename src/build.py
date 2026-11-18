#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/*.json 内联进 src/template.html，输出根目录 index.html。

产物是零依赖单文件：双击就能打开，不需要起服务器、不需要联网。
改了 data/ 下任意 json 后重跑一次即可：python3 src/build.py

也支持换一套数据 / 模板 / 输出位置（看板项目复用它生成自己的日历页）：
    python3 src/build.py --data /path/to/data --template tpl.html --out out.html
"""

import argparse
import datetime
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent

FILES = {
    "events": ("events.json", []),
    "earnings": ("earnings.json", {"updated": "", "items": []}),
    "finance": ("finance.json", {"updated": "", "items": []}),
    "companies": ("companies.json", {"companies": {}}),
    "macro": ("macro.json", {"templates": []}),
    "schedule": ("schedule.json", {"updated": "", "sources": [], "items": []}),
    "actuals": ("actuals.json", {"updated": "", "items": {}}),
    "quotes": ("quotes.json", {"updated": "", "items": {}}),
    "releases": ("releases.json", {"updated": "", "items": {}}),
}


def load(data_dir, name, default):
    p = data_dir / name
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(BASE / "data"), help="数据目录（默认 data/）")
    ap.add_argument("--template", default=str(BASE / "src" / "template.html"))
    ap.add_argument("--out", default=str(BASE / "index.html"))
    args = ap.parse_args()

    data_dir = pathlib.Path(args.data).resolve()
    if not data_dir.is_dir():
        raise SystemExit(f"数据目录不存在: {data_dir}")

    payload = {k: load(data_dir, name, default) for k, (name, default) in FILES.items()}

    tpl = pathlib.Path(args.template).read_text(encoding="utf-8")
    for token in ("__DATA__", "__TODAY__"):
        if token not in tpl:
            raise SystemExit(f"模板缺少占位符 {token}")

    html = (
        tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
           .replace("__TODAY__", datetime.date.today().isoformat())
    )

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    cnt = lambda k, f: len(payload[k].get(f, []))
    print(f"OK  {out}  ({len(html):,} 字节)")
    print(f"    普通事件 {len(payload['events'])} · 财经事件 {cnt('finance','items')}"
          f" · 财报 {cnt('earnings','items')} · 公司 {len(payload['companies'].get('companies', {}))}"
          f" · 宏观模板 {cnt('macro','templates')} · 官方日程 {cnt('schedule','items')}"
          f" · 公布结果 {cnt('releases','items')} · 实际业绩 {cnt('actuals','items')}"
          f" · 行情 {cnt('quotes','items')}")


if __name__ == "__main__":
    main()
