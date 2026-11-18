#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓宏观数据的公布结果，写入 data/releases.json。

只用于**已经公布**的事件：从官方新闻稿标题里取真实数字，不推断、不补全。
同时生成一句事实性摘要（本期 vs 上期），观点性的「解读」字段留给人写，
脚本永远不会覆盖已有的 read 字段。

目前接的是国家统计局「数据发布」栏目（UTF-8 静态页，标题里直接带数字）。
BLS / BEA 的新闻稿在数据中心 IP 上会被拦，抓不到就保留上次结果。

用法：
    python3 scripts/fetch_releases.py            # 抓最近若干页
    python3 scripts/fetch_releases.py --pages 3
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "releases.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

LIST = "https://www.stats.gov.cn/sj/zxfb/"
DETAIL = "https://www.stats.gov.cn/sj/zxfb/{path}"

# 标题 -> (tid, 指标名, 单位)
PATTERNS = [
    (r"(\d{4})年(\d{1,2})月份居民消费价格同比(上涨|下降)([\d.]+)%", "cn-cpi", "CPI 同比", "%"),
    (r"居民消费价格环比(上涨|下降)([\d.]+)%", "cn-cpi", "CPI 环比", "%"),
    (r"(\d{4})年(\d{1,2})月份工业生产者出厂价格同比(上涨|下降)([\d.]+)%", "cn-ppi", "PPI 同比", "%"),
    (r"工业生产者出厂价格环比(上涨|下降)([\d.]+)%", "cn-ppi", "PPI 环比", "%"),
    (r"规模以上工业增加值增长([\d.]+)%", "cn-activity", "工业增加值同比", "%"),
    (r"社会消费品零售总额(增长|下降)([\d.]+)%", "cn-activity", "社零同比", "%"),
    (r"固定资产投资.{0,6}(增长|下降)([\d.]+)%", "cn-activity", "固投同比", "%"),
    (r"采购经理指数.{0,8}为([\d.]+)%", "cn-pmi", "制造业 PMI", ""),
]


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def rows_from(html):
    """列表页 -> [(相对路径, 标题, 发布日期)]

    日期直接取文件名里的 tYYYYMMDD，比在正文里找可靠得多。
    """
    out, seen = [], set()
    for m in re.finditer(r'href="([^"]*?(t\d{8}_\d+\.html))"[^>]*>([^<]{6,90})<', html):
        path, title = m.group(1), " ".join(m.group(3).split())
        if path in seen:
            continue
        seen.add(path)
        dm = re.search(r"t(20\d{2})(\d{2})(\d{2})_", path)
        date = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}" if dm else ""
        out.append((path, title, date))
    return out


def parse_title(title, date):
    """标题 -> [(tid, 指标名, 值, 单位)]"""
    hits = []
    for pat, tid, kname, unit in PATTERNS:
        m = re.search(pat, title)
        if not m:
            continue
        groups = m.groups()
        sign = -1 if ("下降" in groups) else 1
        val = [g for g in groups if re.match(r"^[\d.]+$", str(g))]
        if not val:
            continue
        try:
            v = sign * float(val[-1])
        except ValueError:
            continue
        hits.append((tid, kname, v, unit))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    old = {"items": {}, "updated": ""}
    if OUT.is_file():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    old.setdefault("items", {})

    titles = []
    for i in range(args.pages):
        url = LIST if i == 0 else f"{LIST}index_{i}.html"
        try:
            html = get(url)
        except Exception as e:
            print(f"  ✗ 第 {i+1} 页 {type(e).__name__}: {e}", file=sys.stderr)
            continue
        got = rows_from(html)
        print(f"  ✓ 第 {i+1} 页 {len(got)} 条")
        titles.extend(got)

    # 按公布日聚合指标
    by_key = {}
    for path, title, date in titles:
        if not date:
            continue
        for tid, kname, val, unit in parse_title(title, date):
            key = f"{tid}@{date}"
            rec = by_key.setdefault(key, {"tid": tid, "d": date, "vals": [], "titles": []})
            if not any(v["k"] == kname for v in rec["vals"]):
                rec["vals"].append({"k": kname, "v": val, "u": unit})
            rec["titles"].append(title)

    # 先全部落表，再算摘要，否则「上一个公布期」还没进表
    added = 0
    for key, rec in by_key.items():
        prev = old["items"].get(key) or {}
        item = {
            "tid": rec["tid"], "d": rec["d"], "vals": rec["vals"],
            "title": rec["titles"][0],
            "read": prev.get("read", ""),
            "auto": "",
            "src": "国家统计局",
            "got": dt.date.today().isoformat(),
        }
        if not prev.get("vals"):
            added += 1
        old["items"][key] = item
    for key in sorted(by_key):
        it = old["items"][key]
        it["auto"] = auto_summary(it["tid"], it["d"], it["vals"], old["items"])

    old["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    old["note"] = ("宏观数据公布结果。key = 模板id@公布日。vals 为官方新闻稿里的真实数字；"
                   "auto 是脚本按「本期 vs 上期」生成的事实摘要；read 是人工解读，脚本不覆盖。")

    print(f"整理出 {len(by_key)} 个公布日的指标，新增 {added} 条")
    for key in sorted(by_key)[-6:]:
        it = old["items"][key]
        print("  ", key, " | ".join(f"{v['k']} {v['v']}{v['u']}" for v in it["vals"]))
    if args.dry:
        return
    OUT.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {OUT}")


def auto_summary(tid, date, vals, items):
    """一句事实性摘要：本期 vs 同指标上一个公布期。观点留给 read。"""
    parts = []
    prev_date = None
    for key in sorted(items):
        if key.startswith(tid + "@") and key.split("@")[1] < date:
            prev_date = key
    for v in vals:
        line = f"{v['k']} {v['v']:+.1f}{v['u']}" if v["u"] == "%" else f"{v['k']} {v['v']}{v['u']}"
        if prev_date:
            old = items[prev_date]
            same = next((x for x in old.get("vals", []) if x["k"] == v["k"]), None)
            if same is not None:
                diff = v["v"] - same["v"]
                if abs(diff) < 0.05:
                    line += f"，与前值 {same['v']}{v['u']} 持平"
                else:
                    line += f"，较前值 {same['v']}{v['u']} {'回升' if diff > 0 else '回落'} {abs(diff):.1f} 个百分点"
        parts.append(line)
    return "；".join(parts) + "。" if parts else ""


if __name__ == "__main__":
    main()
