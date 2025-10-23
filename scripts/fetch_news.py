#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
import hashlib
import re
from datetime import datetime, timedelta, timezone
from dateutil import tz, parser as dtparser
import feedparser, yaml
import argparse
from typing import List, Tuple, Dict, Any

# --------- Constantes de tempo / fuso ----------
TZ_LISBON = tz.gettz("Europe/Lisbon")

# --------- Classificação básica por keywords ----------
CATEGORY_KEYWORDS = {
    "Crypto": [
        "bitcoin","btc","ethereum","eth","crypto","blockchain","defi","nft",
        "solana","binance","coinbase","etf bitcoin","etf ether"
    ],
    "US Macro": [
        "fed","fomc","cpi","ppi","payrolls","jobless","inflation","pce","rates",
        "yields","treasury","usd","unemployment","housing starts","ism"
    ],
    "Regulation": [
        "sec","cftc","doj","ftc","lawsuit","settlement","subpoena","investigation",
        "regulation","regulatório","regulacao","compliance"
    ],
    "Earnings": [
        "earnings","results","quarter","guidance","eps","revenue","outlook",
        "lucros","trimestre","balanço","balanco"
    ],
    "Markets": [
        "stocks","equities","nasdaq","dow","s&p","s&p 500","sp500","futures","futuros",
        "options","commodities","oil","gold","silver","treasuries","web3"
    ]
}

def guess_category(title: str, summary: str, source_name: str) -> str:
    text = f"{title} {summary} {source_name}".lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                return cat
    # fallback: pelo nome da fonte
    s = (source_name or "").lower()
    if any(x in s for x in ["coindesk","cointelegraph","decrypt","the block"]):
        return "Crypto"
    if any(x in s for x in ["reuters","wsj","yahoo","ft"]):
        return "Markets"
    return "Markets"

# --------- Filtros anti-lixo ----------
IGNORE_TITLE_PATTERNS = [
    r"^CoinDesk:\s*Bitcoin,\s*Ethereum,\s*Crypto News and Price Data$",
]

def should_ignore(title: str, link: str) -> bool:
    t = (title or "").strip()
    for pat in IGNORE_TITLE_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True

    l = (link or "").lower()

    # CoinDesk: ignora hubs/listagens, não artigos normais
    if "coindesk.com" in l:
        if "/live/" in l:
            return True
        # hubs comuns (categorias, autores, pesquisa, vídeo, etc.)
        if re.search(r"/(category|tags|video|videos|authors|search)(/|$)", l):
            return True

    return False

# --------- Normalização de cada entrada ----------
def normalize_item(entry: Any, feed_title: str) -> Tuple[Dict[str, Any], datetime]:
    title = (entry.get("title") or "").strip()
    link = entry.get("link") or ""
    # corta HTML do summary e limita tamanho
    summary = re.sub("<[^<]+?>", "", (entry.get("summary") or ""))[:280].strip()
    source = feed_title.split(" - ")[0].strip() if feed_title else ""

    # published/updated -> datetime UTC
    dt = None
    if entry.get("published_parsed"):
        dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), timezone.utc)
    elif entry.get("updated_parsed"):
        dt = datetime.fromtimestamp(time.mktime(entry.updated_parsed), timezone.utc)
    else:
        for key in ("published","updated","date"):
            if entry.get(key):
                try:
                    dt = dtparser.parse(entry.get(key)).astimezone(timezone.utc)
                    break
                except Exception:
                    pass
    if not dt:
        dt = datetime.now(timezone.utc)

    # id estável
    raw_id = entry.get("id") or link or title
    uid = hashlib.sha1(raw_id.encode("utf-8","ignore")).hexdigest()[:12]

    return ({
        "id": uid,
        "time_iso": dt.astimezone(TZ_LISBON).isoformat(timespec="minutes"),
        "time": dt.astimezone(TZ_LISBON).strftime("%H:%M"),
        "category": None,  # preenchido depois
        "title": title,
        "summary": summary,
        "source": source,
        "url": link,
        "tags": []
    }, dt)

# --------- Parser de feeds ----------
def collect_from(feeds: List[str]) -> List[Tuple[Dict[str,Any], datetime, str]]:
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

# --------- Dedupe mantendo o mais recente ----------
def dedupe_keep_latest(items_with_dt: List[Tuple[Dict[str,Any], datetime]]) -> List[Tuple[Dict[str,Any], datetime]]:
    best = {}
    for it, dt in items_with_dt:
        key = ((it.get("title") or "")[:120].lower(), (it.get("source") or "").lower())
        cur = best.get(key)
        if (cur is None) or (dt > cur[1]):
            best[key] = (it, dt)
    return list(best.values())

# --------- CLI ----------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7, help="nº de dias de histórico (default: 7)")
    return p.parse_args()

# --------- MAIN ----------
def main():
    args = parse_args()
    lookback_days = max(1, args.days)

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=lookback_days)
    print(f"[fetch] usando lookback_days={lookback_days} (cutoff={cutoff_utc.isoformat()})")

    # ler feeds
    with open("config/feeds.yml","r",encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    all_entries = []
    for group in cfg.values():
        all_entries.extend(collect_from(group))

    # filtra por janela temporal (UTC)
    filtered = [(it,dt,feed_title) for (it,dt,feed_title) in all_entries if dt >= cutoff_utc]

    # remove lixo antes de enriquecer
    filtered = [
        (it, dt, feed_title)
        for (it, dt, feed_title) in filtered
        if not should_ignore(it.get("title"), it.get("url"))
    ]

    # enriquecer com categoria + tags + impact_score
    items_with_dt: List[Tuple[Dict[str,Any], datetime]] = []
    for it, dt, feed_title in filtered:
        # categoria
        cat = guess_category(it["title"], it["summary"], it["source"] or feed_title)
        it["category"] = cat

        # tags rápidas
        t_title = (it["title"] or "").lower()
        tags = []
        if "bitcoin" in t_title or "btc" in t_title: tags.append("BTC")
        if "ethereum" in t_title or "eth" in t_title: tags.append("ETH")
        it["tags"] = tags

        # ---------- impact score (3 = alto, 2 = médio, 1 = baixo) ----------
        t_all = f'{it.get("title","")} {it.get("summary","")}'.lower()
        if any(k in t_all for k in [
            "cpi","pce","fed","fomc","sec","cftc","ban","etf","collapse","lawsuit",
            "bankruptcy","halt","shutdown","tariff","default","treasury","yields"
        ]):
            it["impact_score"] = 3
        elif any(k in t_all for k in [
            "btc","bitcoin","eth","ethereum","earnings","guidance","record","drop",
            "surge","plunge","rally","upgrade","downgrade"
        ]):
            it["impact_score"] = 2
        else:
            it["impact_score"] = 1
        # -------------------------------------------------------------------

        items_with_dt.append((it, dt))

    # ordenar desc por data, dedupe e cap
    items_with_dt.sort(key=lambda x: x[1], reverse=True)
    items_with_dt = dedupe_keep_latest(items_with_dt)
    MAX_ITEMS = 1000
    items_with_dt = items_with_dt[:MAX_ITEMS]  # aplica o limite

    # agrupar por dia (Lisboa)
    days_map: Dict[str, Dict[str, Any]] = {}
    for it, dt in items_with_dt:
        d_lis = dt.astimezone(TZ_LISBON).date().isoformat()
        bucket = days_map.setdefault(d_lis, {"date": d_lis, "attention_points": [], "items": []})

        # não queremos time_iso no JSON final
        it.pop("time_iso", None)
        bucket["items"].append(it)

    # ordenar itens por impacto + hora e preencher os 3 pontos de atenção
    for d in days_map.values():
        d["items"].sort(key=lambda x: (x.get("impact_score", 0), x.get("time", "00:00")), reverse=True)
        for h in d["items"]:
            title_clean = (h.get("title") or "").strip()
            if title_clean:
                d["attention_points"].append(title_clean[:120])
            if len(d["attention_points"]) >= 3:
                break

    days = sorted(days_map.values(), key=lambda d: d["date"], reverse=True)

    payload = {
        "generated_at": datetime.now(TZ_LISBON).isoformat(timespec="minutes"),
        "timezone": "Europe/Lisbon",
        "days": days
    }

    with open("noticias.json","w",encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    total = sum(len(d["items"]) for d in days)
    print(f"[fetch] Wrote noticias.json with {total} items across {len(days)} day(s).")

if __name__ == "__main__":
    main()
