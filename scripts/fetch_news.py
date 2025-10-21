#!/usr/bin/env python3
import json, time, hashlib, re
from datetime import datetime, timedelta, timezone
from dateutil import tz, parser as dtparser
import feedparser, yaml
import argparse

TZ_LISBON = tz.gettz("Europe/Lisbon")
NOW_UTC = datetime.now(timezone.utc)
WINDOW_HOURS = 24  # últimas 24h

CATEGORY_KEYWORDS = {
    "Crypto": ["bitcoin","btc","ethereum","eth","crypto","blockchain","defi","nft","solana","binance","coinbase","etf bitcoin","etf ether"],
    "US Macro": ["fed","fomc","cpi","ppi","payrolls","jobless","inflation","pce","rates","yields","treasury","usd","unemployment","housing starts","ism"],
    "Regulation": ["sec","cftc","doj","ftc","lawsuit","settlement","subpoena","investigation","regulation","regulatório","regulacao","compliance"],
    "Earnings": ["earnings","results","quarter","guidance","EPS","revenue","outlook","lucros","trimestre","balanço","balanco"],
    "Markets": ["stocks","equities","nasdaq","dow","s&p","sp500","futures","futuros","options","commodities","oil","gold","silver","treasuries","web3"]
}

def guess_category(title, summary, source_name):
    text = f"{title} {summary} {source_name}".lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                return cat
    # fallback: map by source
    if any(s in (source_name or "").lower() for s in ["coindesk","cointelegraph","decrypt","the block"]):
        return "Crypto"
    if any(s in (source_name or "").lower() for s in ["reuters","wsj","yahoo","ft"]):
        return "Markets"
    return "Markets"

def normalize_item(entry, feed_title):
    title = (entry.get("title") or "").strip()
    link = entry.get("link") or ""
    summary = re.sub("<[^<]+?>", "", (entry.get("summary") or ""))[:280].strip()
    source = feed_title.split(" - ")[0] if feed_title else ""
    # published date
    dt = None
    if "published_parsed" in entry and entry.published_parsed:
        dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), timezone.utc)
    elif "updated_parsed" in entry and entry.updated_parsed:
        dt = datetime.fromtimestamp(time.mktime(entry.updated_parsed), timezone.utc)
    else:
        # try any date field
        for key in ("published","updated","date"):
            if entry.get(key):
                try:
                    dt = dtparser.parse(entry.get(key)).astimezone(timezone.utc)
                    break
                except Exception:
                    pass
    if not dt:
        dt = NOW_UTC

    # id
    raw_id = entry.get("id") or link or title
    uid = hashlib.sha1(raw_id.encode("utf-8","ignore")).hexdigest()[:12]

    return {
        "id": uid,
        "time_iso": dt.astimezone(TZ_LISBON).isoformat(timespec="minutes"),
        "time": dt.astimezone(TZ_LISBON).strftime("%H:%M"),
        "category": None,  # to be filled later
        "title": title,
        "summary": summary,
        "source": source,
        "url": link,
        "tags": []
    }, dt

def collect_from(feeds: list):
    out = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            feed_title = (parsed.feed.get("title") if hasattr(parsed, "feed") else "") or ""
            for e in parsed.entries:
                item, dt = normalize_item(e, feed_title)
                out.append((item, dt, feed_title))
        except Exception as e:
            print("ERR feed", url, e)
    return out

def dedupe(items):
    seen = set()
    out = []
    for it in items:
        key = (it["title"][:120].lower(), it["source"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=2, help="nº de dias de histórico a incluir (default: 2)")
    return p.parse_args()

def main():

    args = parse_args()
lookback_days = max(1, args.days)

# agora (UTC) e cutoff (UTC)
now_utc = datetime.now(timezone.utc)
cutoff_utc = now_utc - timedelta(days=lookback_days)

# fuso para Lisboa (para agrupar por dia e formatar horas)
tz_local = tz.gettz("Europe/Lisbon")
print(f"[fetch] usando lookback_days={lookback_days} (cutoff={cutoff_utc.isoformat()})")

    with open("config/feeds.yml","r",encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    all_entries = []
    for group in cfg.values():
        all_entries.extend(collect_from(group))

    # filter by last 24h
    cutoff = NOW_UTC - timedelta(hours=WINDOW_HOURS)
    filtered = [(it,dt,feed_title) for (it,dt,feed_title) in all_entries if dt >= cutoff]

    # build items with categories
    items = []
    for it, dt, feed_title in filtered:
        cat = guess_category(it["title"], it["summary"], it["source"] or feed_title)
        it["category"] = cat
        # create some tags quickly
        tags = []
        t = it["title"].lower()
        if "bitcoin" in t or "btc" in t: tags.append("BTC")
        if "ethereum" in t or "eth" in t: tags.append("ETH")
        it["tags"] = tags
        items.append((it, dt))

    # sort newest -> old
    items.sort(key=lambda x: x[1], reverse=True)

    # keep a sensible cap (avoid bloating file)
    MAX_ITEMS = 120
    items = items[:MAX_ITEMS]

    # group by date (Lisbon)
    days_map = {}
    for it, dt in items:
        d_lis = dt.astimezone(TZ_LISBON).date().isoformat()
        days_map.setdefault(d_lis, {"date": d_lis, "attention_points": [], "items": []})
        # drop ISO helper
        it.pop("time_iso", None)
        days_map[d_lis]["items"].append(it)

    # simple attention points (top headlines)
    for d in days_map.values():
        for h in d["items"][:3]:
            d["attention_points"].append(h["title"][:90])

    days = sorted(days_map.values(), key=lambda d: d["date"], reverse=True)

    payload = {
        "generated_at": datetime.now(TZ_LISBON).isoformat(timespec="minutes"),
        "timezone": "Europe/Lisbon",
        "days": days
    }

    with open("noticias.json","w",encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Wrote noticias.json with {sum(len(d['items']) for d in days)} items across {len(days)} day(s).")

if __name__ == "__main__":
    main()
