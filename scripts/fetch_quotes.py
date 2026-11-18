#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓财报次日股价反应，写入 data/quotes.json。

数据源 Yahoo Finance 公开行情接口（无需 key）。财报日及前后一个交易日的
收盘价足够算「公布后股价表现」，所以只取 3 个月日线。

用法：
    python3 scripts/fetch_quotes.py              # 抓 earnings.json 里所有公司
    python3 scripts/fetch_quotes.py --only JD,3690.HK
    python3 scripts/fetch_quotes.py --all        # 连历史财报日一起补

口径：
    pre       财报日前一个交易日收盘
    close     财报当日收盘（盘前发布看当天，盘后发布看当天）
    next      财报次日收盘
    chg       close 相对 pre 的涨跌幅
    next_chg  next 相对 close 的涨跌幅 —— 盘后发布的公司主要看这个
"""

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
QUOTES = BASE / "data" / "quotes.json"
EARN = BASE / "data" / "earnings.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def yahoo_symbol(ticker):
    """600519 -> 600519.SS，000001 -> 000001.SZ，其余原样。"""
    t = str(ticker).strip().upper()
    if not t:
        return ""
    if t.endswith(".HK") or t.endswith(".SS") or t.endswith(".SZ") or t.endswith(".SH"):
        return t.replace(".SH", ".SS")
    if t.isdigit() and len(t) == 6:
        return t + (".SS" if t[0] == "6" else ".SZ")
    return t


class Yahoo:
    """Yahoo 需要先拿 cookie 再拿 crumb，否则 chart 接口 401。"""

    def __init__(self):
        self.cookie = ""
        self.crumb = ""

    def _open(self, url, timeout=25):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            sc = r.headers.get("Set-Cookie")
            if sc:
                self.cookie = "; ".join(
                    c.split(";")[0] for c in self.cookie.split("; ") if c
                ) if self.cookie else sc.split(";")[0]
                if sc.split(";")[0] not in self.cookie:
                    self.cookie += "; " + sc.split(";")[0]
            return body

    def handshake(self):
        for u in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
            try:
                self._open(u)
                break
            except Exception:
                continue
        try:
            self.crumb = self._open(
                "https://query1.finance.yahoo.com/v1/test/getcrumb").decode()
        except Exception:
            self.crumb = ""

    def daily(self, symbol, months=3):
        p = urllib.parse.urlencode({
            "range": f"{months}mo", "interval": "1d", "events": "div,split"})
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{p}"
        if self.crumb:
            url += "&crumb=" + urllib.parse.quote(self.crumb)
        raw = self._open(url)
        d = json.loads(raw)
        res = d.get("chart", {}).get("result") or []
        if not res:
            raise RuntimeError(f"{symbol} 无行情数据")
        r0 = res[0]
        ts = r0.get("timestamp") or []
        cl = (r0.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        out = []
        for t, c in zip(ts, cl):
            if c is None:
                continue
            out.append((dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), float(c)))
        out.sort()
        return out


def pick(series, day):
    """返回 (day 前最后一个交易日, <=day 的最后一个交易日, 之后第一个交易日)。"""
    prev = [x for x in series if x[0] < day]
    upto = [x for x in series if x[0] <= day]
    after = [x for x in series if x[0] > day]
    return (prev[-1] if prev else None,
            upto[-1] if upto else None,
            after[0] if after else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    earn = json.loads(EARN.read_text(encoding="utf-8")); 
    q = {"items": {}, "updated": "", "note": ""}
    if QUOTES.is_file():
        try:
            q = json.loads(QUOTES.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    q.setdefault("items", {})

    rows = []
    for it in earn.get("items", []):
        tk = yahoo_symbol(it.get("ticker", ""))
        d = it.get("d", "")
        if not tk or not d:
            continue
        if args.only and tk not in args.only.split(",") and it.get("ticker") not in args.only.split(","):
            continue
        rows.append((tk, d, it))

    y = Yahoo()
    try:
        y.handshake()
    except Exception as e:
        print(f"  握手失败：{e}", file=sys.stderr)

    ok = 0
    for tk, d, it in rows:
        try:
            series = y.daily(tk, args.months)
            day = dt.date.fromisoformat(d)
            pre, cur, nxt = pick(series, day)
            if not cur:
                print(f"  · {tk:10s} {d} 无当日行情（未上市/停牌？）")
                continue
            rec = {
                "report_date": d,
                "pre": pre[1] if pre else None,
                "close": cur[1],
                "next": nxt[1] if nxt else None,
                "chg": round((cur[1] / pre[1] - 1) * 100, 2) if pre else None,
                "next_chg": round((nxt[1] / cur[1] - 1) * 100, 2) if nxt else None,
                "asof": series[-1][0].isoformat(),
                "src": "yahoo",
            }
            q["items"][tk] = rec
            ok += 1
            print(f"  ✓ {tk:10s} {d}  当日 {rec['chg'] if rec['chg'] is not None else '-':>7}%"
                  f"  次日 {rec['next_chg'] if rec['next_chg'] is not None else '-':>7}%")
            time.sleep(0.4)   # 别把公开接口打急
        except Exception as e:
            print(f"  ✗ {tk:10s} {type(e).__name__}: {e}", file=sys.stderr)

    q["updated"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    q["note"] = QUOTES_TODO = ("财报次日股价反应。pre=财报前一日收盘，close=财报当日收盘，"
                               "next=次日收盘；chg / next_chg 为涨跌幅（%）。")
    print(f"合计 {ok} 家")
    if args.dry:
        return
    QUOTES.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {QUOTES}")


if __name__ == "__main__":
    main()
