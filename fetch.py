"""财报日历数据更新脚本：用 yfinance 查每家公司下一次财报时间，输出 data.json。
用法：pip install -r requirements.txt && python fetch.py
改关注列表只需改下面的 WATCHLIST。不需要任何 API key、账号或密码。
GitHub Actions 每天会自动运行它（见 .github/workflows/update.yml）。
"""
import json, datetime as dt, math, time
from zoneinfo import ZoneInfo
import yfinance as yf

ET = ZoneInfo("America/New_York")
BJ = ZoneInfo("Asia/Shanghai")

# 分组 → [(代码, 中文名)]
WATCHLIST = {
    "持仓": [("RDDT", "Reddit"), ("TCOM", "携程"), ("0700.HK", "腾讯"),
             ("CRCL", "Circle"), ("BNC", "BNB Standard")],
    "观察": [("NET", "Cloudflare"), ("CROX", "Crocs"), ("INTC", "英特尔")],
    "科技龙头": [("AAPL", "苹果"), ("MSFT", "微软"), ("GOOGL", "谷歌"), ("AMZN", "亚马逊"),
              ("META", "Meta"), ("NVDA", "英伟达"), ("TSLA", "特斯拉")],
}

RECENT_DAYS = 10  # 最近几天内发布过的，单独显示「刚发布」


def num(x):
    try:
        x = float(x)
        return None if math.isnan(x) else round(x, 2)
    except (TypeError, ValueError):
        return None


def session_label(ts_utc, is_hk):
    """根据美东/香港当地时间判断盘前、盘后。"""
    if is_hk:
        local = ts_utc.astimezone(ZoneInfo("Asia/Hong_Kong"))
        return "港股收盘后" if local.hour >= 16 else ("港股开盘前" if local.hour < 9 else "时段未公布")
    local = ts_utc.astimezone(ET)
    m = local.hour * 60 + local.minute
    if m < 9 * 60 + 30:
        return "美股盘前"
    if m >= 16 * 60:
        return "美股盘后"
    return "时段未公布"  # yfinance 对没公布具体时间的通常给 15:00 ET 之类的占位


def one(symbol, cn, group, now):
    tk = yf.Ticker(symbol)
    is_hk = symbol.endswith(".HK")
    item = {"symbol": symbol, "name": cn, "group": group, "next": None, "last": None}

    try:
        info = tk.info or {}
    except Exception:
        info = {}
    try:
        df = tk.get_earnings_dates(limit=8)
    except Exception:
        df = None

    rows = []
    if df is not None and len(df):
        for idx, r in df.iterrows():
            ts = idx.to_pydatetime().astimezone(dt.timezone.utc)
            rows.append({"ts": ts, "est": num(r.get("EPS Estimate")),
                         "rep": num(r.get("Reported EPS")), "sur": num(r.get("Surprise(%)"))})

    # 下一次：优先用公司已确认的时间，否则用 yfinance 的预估
    nxt, confirmed = None, False
    ts_info = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
    if ts_info:
        t = dt.datetime.fromtimestamp(ts_info, dt.timezone.utc)
        if t > now - dt.timedelta(hours=12):
            nxt, confirmed = t, not info.get("isEarningsDateEstimate", True)
    future = sorted([r for r in rows if r["ts"] > now - dt.timedelta(hours=12) and r["rep"] is None],
                    key=lambda r: r["ts"])
    if nxt is None and future:
        nxt = future[0]["ts"]
    if nxt:
        est = next((r["est"] for r in future if abs((r["ts"] - nxt).days) <= 2), None)
        item["next"] = {
            "utc": nxt.isoformat(),
            "bj": nxt.astimezone(BJ).strftime("%Y-%m-%d %H:%M"),
            "local_date": nxt.astimezone(ZoneInfo("Asia/Hong_Kong") if is_hk else ET).strftime("%Y-%m-%d"),
            "session": session_label(nxt, is_hk),
            "confirmed": bool(confirmed),
            "eps_est": est,
        }

    # 最近一次已发布
    past = sorted([r for r in rows if r["rep"] is not None and r["ts"] <= now], key=lambda r: r["ts"], reverse=True)
    if past:
        p = past[0]
        item["last"] = {"utc": p["ts"].isoformat(), "bj": p["ts"].astimezone(BJ).strftime("%Y-%m-%d"),
                        "eps": p["rep"], "est": p["est"], "surprise": p["sur"],
                        "recent": (now - p["ts"]).days <= RECENT_DAYS}
    return item


def load_old():
    """读上一次的 data.json，某家公司这次没查到时沿用旧数据。"""
    try:
        with open("data.json", encoding="utf-8") as f:
            return {it["symbol"]: it for it in json.load(f).get("items", [])}
    except (OSError, ValueError):
        return {}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    old = load_old()
    items = []
    for group, lst in WATCHLIST.items():
        for sym, cn in lst:
            item = None
            for attempt in range(3):  # Yahoo 偶尔限流，失败就等一会儿重试
                try:
                    item = one(sym, cn, group, now)
                    if item["next"] or item["last"]:
                        break
                except Exception as e:
                    item = {"symbol": sym, "name": cn, "group": group, "next": None, "last": None,
                            "error": str(e)[:120]}
                time.sleep(20 * (attempt + 1))
            prev = old.get(sym)
            if prev and not item.get("next") and prev.get("next") \
                    and dt.datetime.fromisoformat(prev["next"]["utc"]) > now:
                item["next"] = prev["next"]  # 这次没查到，沿用上次的
                if not item.get("last"):
                    item["last"] = prev.get("last")
            items.append(item)
            print(sym, "ok" if item.get("next") else "无下一次日期")
            time.sleep(1)
    out = {"updated_utc": now.isoformat(), "updated_bj": now.astimezone(BJ).strftime("%Y-%m-%d %H:%M"),
           "items": items}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
