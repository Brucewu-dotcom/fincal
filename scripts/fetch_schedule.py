#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从官方站点抓取宏观数据发布日程，写入 data/schedule.json。

只抓官方页面，不猜日期。某个站点抓不到时保留上一次的条目，并在
sources[].ok 里标 false，页面会显示「该来源暂时不可用」。

用法：
    python3 scripts/fetch_schedule.py            # 抓取并更新
    python3 scripts/fetch_schedule.py --dry      # 只打印，不写文件
    python3 scripts/fetch_schedule.py --only fed,nbs

时间统一换算成北京时间（Asia/Shanghai），存到 t 字段；原始口径写进 note。
"""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "schedule.json"

BJ = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")
KEEP_DAYS_BACK = 45          # 保留多久以前的历史条目
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

SOURCES = {
    "fed": ("美联储 FOMC 会议日历",
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
    "bls": ("美国劳工统计局 发布日程",
            "https://www.bls.gov/schedule/{year}/home.htm"),
    "bea": ("美国经济分析局 发布日程", "https://www.bea.gov/news/schedule"),
    "census": ("美国人口普查局 经济指标",
               "https://www.census.gov/economic-indicators/calendar-list.html"),
    "nbs": ("国家统计局 主要统计信息发布日程表",
            "https://www.stats.gov.cn/xxgk/sjfb/fbrcb/"),
}

# 关键词 -> 模板 id。顺序敏感，先匹配到的赢。
RULES = [
    # 美国
    ("fomc", ["us-fomc"]),
    ("Consumer Price Index", ["us-cpi"]),
    ("Producer Price Index", ["us-ppi"]),
    ("Employment Situation ", ["us-nfp"]),
    ("Job Openings and Labor Turnover", ["us-jolts"]),
    ("Import and Export Price", ["us-import-price"]),
    ("Personal Income and Outlays", ["us-pce"]),
    ("GDP", ["us-gdp"]),
    ("International Trade in Goods and Services", ["us-trade"]),
    ("Advance Retail and Food Services Sales", ["us-retail"]),
    ("Housing Starts", ["us-housing"]),
    # 中国
    ("居民消费价格指数月度报告", ["cn-cpi"]),
    ("工业生产者价格指数月度报告", ["cn-ppi"]),
    ("国民经济运行情况", ["cn-activity"]),
    ("采购经理指数月度报告", ["cn-pmi"]),
    ("工业经济效益月度报告", ["cn-profit"]),
]


def map_tids(title):
    """把官方发布名称映射到模板 id，没匹配上返回空列表。"""
    t = " ".join(title.split())
    for bad in ("by County", "by State", "State Personal Income", "State GDP",
                "State Annual", "Metropolitan Area", "County Employment"):
        if bad.lower() in t.lower():
            return []
    for kw, ids in RULES:
        if kw.lower() in t.lower():
            return ids
    return []


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def beijing_date(local_date, hhmm, tz):
    """把当地日期+时刻换算成北京时间。返回 (YYYY-MM-DD, HH:MM)。

    hhmm 为空时只换算日期（按当地 12:00 锚定，避免跨日误判）。
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", local_date)
    if not m:
        return None, ""
    y, mo, d = (int(x) for x in m.groups())
    if hhmm and re.match(r"^\d{1,2}:\d{2}$", hhmm):
        h, mi = (int(x) for x in hhmm.split(":"))
    else:
        h, mi = 12, 0
    src = dt.datetime(y, mo, d, h, mi, tzinfo=tz)
    dst = src.astimezone(BJ)
    return dst.date().isoformat(), (f"{dst.hour:02d}:{dst.minute:02d}" if hhmm else "")


def item(tid, name, d, t, imp, c, src, note="", period="", url=""):
    return {
        "tid": tid, "name": name, "d": d, "t": t, "imp": imp,
        "c": c, "src": src, "note": note, "period": period, "url": url,
    }


# ── 美联储 FOMC ──────────────────────────────────────────────
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 1)}


def fetch_fed():
    url = SOURCES["fed"][1]
    html = get(url)
    out = []
    # 每个年份是一个 panel，取后续 fomc-meeting 行
    for m in re.finditer(r'<a id="\d+">(\d{4}) FOMC Meetings</a>', html):
        year = int(m.group(1))
        seg = html[m.end(): m.end() + 20000]
        stop = re.search(r'<a id="\d+">\d{4} FOMC Meetings</a>', seg)
        if stop:
            seg = seg[:stop.start()]
        rows = re.findall(
            r'fomc-meeting__month[^>]*><strong>(\w+)</strong>.*?'
            r'fomc-meeting__date[^>]*>([^<]+)<', seg, re.S)
        for month_name, rng in rows:
            mon = MONTHS.get(month_name)
            if not mon:
                continue
            # 「17-18*」的星号标 SEP 会议、「22 (notation vote)」是特殊安排
            rng = re.sub(r"\(.*?\)", "", rng).replace("*", "").strip()
            mm = re.match(r"(\d{1,2})-(\d{1,2})$", rng)
            if mm:
                day = int(mm.group(2))          # 决议在第二天
            else:
                d1 = re.match(r"(\d{1,2})$", rng)
                if not d1:
                    continue
                day = int(d1.group(1))
            d, t = beijing_date(f"{year}-{mon:02d}-{day:02d}", "14:00", ET)
            out.append(item(
                "us-fomc", "FOMC 利率决议", d, t, "high", "US", "fed",
                note=f"美东 {mon:02d}-{day:02d} 14:00 决议 + 发布会",
                url=url))
    return out, len(out)


# ── BLS ─────────────────────────────────────────────────────
def fetch_bls():
    out, seen, errs = [], set(), []
    year = dt.date.today().year
    for y in (year, year + 1):
        url = SOURCES["bls"][1].format(year=y)
        try:
            html = get(url)
        except Exception as e:
            errs.append(str(e))
            continue
        if "<tr" not in html:
            errs.append("页面无表格，可能被反爬拦截")
            continue
        # 行结构：<td>Friday, January 01, 2027</td> ... 时间 ... Release
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            cells = [re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
                     for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
            cells = [" ".join(c.split()) for c in cells]
            if len(cells) < 2:
                continue
            dm = re.search(r"(\w+day),\s+(\w+)\s+(\d{1,2}),\s+(\d{4})", cells[0])
            if not dm:
                continue
            mon = MONTHS.get(dm.group(2))
            if not mon:
                continue
            day = int(dm.group(3))
            y2 = int(dm.group(4))
            tm = ""
            for c in cells[1:]:
                tm2 = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", c, re.I)
                if tm2:
                    h = int(tm2.group(1)) % 12
                    if tm2.group(3).upper() == "PM":
                        h += 12
                    tm = f"{h:02d}:{tm2.group(2)}"
                    break
            title = cells[-1]
            if not tm or not title:
                continue
            tids = map_tids(title)
            if not tids:
                continue
            d, t = beijing_date(f"{y2}-{mon:02d}-{day:02d}", tm, ET)
            for tid in tids:
                if (tid, d) in seen:
                    continue
                seen.add((tid, d))
                out.append(item(
                    tid, "", d, t, "high" if tid in ("us-cpi", "us-nfp") else "mid",
                    "US", "bls",
                    note=f"{title}｜美东 {mon:02d}-{day:02d} {tm}",
                    url=url))
    if not out:
        raise RuntimeError("抓不到任何数据；" + "；".join(errs[:2]))
    return out, len(out)


# ── BEA ─────────────────────────────────────────────────────
def fetch_bea():
    url = SOURCES["bea"][1]
    html = get(url)
    out = []
    cur_year = dt.date.today().year
    for block in re.finditer(
            r'<div class="release-date">([\w ]+\d{1,2})</div>\s*'
            r'<small class="text-muted">([\d:]+ ?[AP]M)</small>.*?'
            r'<td class="release-title[^"]*"[^>]*>(.*?)</td>', html, re.S):
        date_s, time_s, title = block.group(1), block.group(2), block.group(3)
        title = " ".join(re.sub(r"<[^>]+>", " ", title).split())
        mon = MONTHS.get(date_s.split()[0])
        if not mon:
            continue
        day = int(date_s.split()[1])
        # 页面是按年筛选的，年份取自表格表头
        hy = re.search(r"Year (\d{4})", html)
        year = int(hy.group(1)) if hy else cur_year
        tm = ""
        mt = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", time_s.upper())
        if mt:
            h = int(mt.group(1)) % 12 + (12 if mt.group(3) == "PM" else 0)
            tm = f"{h:02d}:{mt.group(2)}"
        tids = map_tids(title)
        if not tids:
            continue
        d, t = beijing_date(f"{year}-{mon:02d}-{day:02d}", tm, ET)
        for tid in tids:
            out.append(item(tid, "", d, t, "high", "US", "bea",
                            note=f"{title}｜美东 {mon:02d}-{day:02d} {tm}", url=url))
    return out, len(out)


# ── 美国人口普查局（页面只给「下次发布」，且对数据中心 IP 有反爬） ──
def fetch_census():
    url = SOURCES["census"][1]
    html = get(url)
    out = []
    for block in re.finditer(
            r"Released.{0,120}?([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th), \d{4})"
            r".{0,600}?Next Release.{0,120}?([A-Z][a-z]+ \d{1,2}(?:st|nd|rd|th), \d{4})",
            html, re.S):
        pass  # 该页为前端渲染 + 反爬，解析稳定前不启用
    if not out:
        raise RuntimeError("页面无可用结构化日程（403 或前端渲染）")
    return out, len(out)


# ── 国家统计局 ───────────────────────────────────────────────
def fetch_nbs():
    """解析「主要统计信息发布日程表」：日期行 + 紧随其后的时间行。"""
    index = SOURCES["nbs"][1]
    html = None
    url = ""
    try:
        idx = get(index)
        links = re.findall(
            r'href="(?:\./)?(\d{6}/t\d{8}_\d+\.html)"[^>]*>[^<]*主要统计信息发布日程表', idx)
        if links:
            newest = sorted(links)[-1]
            url = index.rstrip("/") + "/" + newest
            html = get(url)
    except Exception:
        pass
    if not html:
        raise RuntimeError("日程表索引页不可用")

    tm = re.search(r"<table.*?</table>", html, re.S)
    if not tm:
        raise RuntimeError("未找到日程表")
    table = tm.group(0)

    # 年份：标题里的「2026年」
    ym = re.search(r"(\d{4})年国家统计局", html)
    year = int(ym.group(1)) if ym else dt.date.today().year

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    cells_of = lambda r: [" ".join(re.sub(r"<[^>]+>", " ", c)
                                     .replace("\u3000", " ").split())
                          for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]

    out = []
    i = 0
    while i < len(rows):
        cells = cells_of(rows[i])
        if len(cells) >= 14 and re.match(r"^\d+$", cells[0]):
            name = cells[1]
            months = cells[2:14]
            times = cells_of(rows[i + 1]) if i + 1 < len(rows) else []
            # 时间行只给有日期的月份，按出现顺序对齐
            slots = [(k + 1, v) for k, v in enumerate(months)
                     if re.match(r"^\d{1,2}/", v)]
            tlist = [x for x in times if re.match(r"^\d{1,2}:\d{2}$", x)]
            for (mon, v), t in zip(slots, tlist + [""] * 12):
                day = int(v.split("/")[0])
                tids = map_tids(name)
                if not tids:
                    continue
                d = f"{year}-{mon:02d}-{day:02d}"
                for tid in tids:
                    out.append(item(tid, "", d, t, "high" if "消费价格" in name or
                                    "国民经济" in name else "mid", "CN", "nbs",
                                    note=name, url=url))
            i += 2
        else:
            i += 1
    return out, len(out)


FETCHERS = {
    "fed": fetch_fed, "bls": fetch_bls, "bea": fetch_bea,
    "census": fetch_census, "nbs": fetch_nbs,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不写文件")
    ap.add_argument("--only", default="", help="只跑指定来源，逗号分隔")
    ap.add_argument("--window", type=int, default=KEEP_DAYS_BACK)
    args = ap.parse_args()

    old = {"items": [], "sources": []}
    if OUT.is_file():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    wanted = [s for s in (args.only.split(",") if args.only else FETCHERS) if s in FETCHERS]
    fresh, sources = [], []
    for sid in FETCHERS:
        name, url = SOURCES[sid]
        url = url.format(year=dt.date.today().year) if "{year}" in url else url
        rec = {"id": sid, "name": name, "url": url, "ok": False,
               "count": 0, "checked": dt.date.today().isoformat(), "err": ""}
        if sid not in wanted:
            rec.update(ok=True, count=0, err="skip")
            sources.append(rec)
            continue
        try:
            items, n = FETCHERS[sid]()
            fresh.extend(items)
            rec.update(ok=True, count=n)
            print(f"  ✓ {sid:7s} {n:3d} 条")
        except Exception as e:  # 网络/反爬/改版都不该让整次抓取失败
            rec["err"] = f"{type(e).__name__}: {e}"[:160]
            print(f"  ✗ {sid:7s} {rec['err']}", file=sys.stderr)
        sources.append(rec)

    # 抓取失败的来源：沿用旧条目
    failed = {s["id"] for s in sources if not s["ok"]}
    if failed:
        for it in old.get("items", []):
            if it.get("src") in failed:
                fresh.append(it)
                print(f"  · 沿用旧数据 {it['src']}: {it['tid']} {it['d']}")

    # 去重 + 排序 + 裁剪
    today = dt.date.today()
    floor = (today - dt.timedelta(days=args.window)).isoformat()
    seen, merged = set(), []
    for it in sorted(fresh, key=lambda x: (x["d"], x.get("t", ""))):
        k = (it["tid"], it["d"], it.get("t", ""))
        if k in seen or it["d"] < floor:
            continue
        seen.add(k)
        merged.append(it)

    payload = {
        "updated": dt.datetime.now(BJ).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "note": "官方发布日程快照。日期时间已换算为北京时间；"
                "美国数据来源的原始口径写在 note 里。抓不到时不猜，沿用上次结果。",
        "sources": sources,
        "items": merged,
    }

    print(f"合计 {len(merged)} 条，覆盖 "
          f"{min((x['d'] for x in merged), default='-')} → "
          f"{max((x['d'] for x in merged), default='-')}")
    if args.dry:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:3000])
        return
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {OUT}")


if __name__ == "__main__":
    main()
