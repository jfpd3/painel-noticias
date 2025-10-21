#!/usr/bin/env python3
import json, time, hashlib, re
from datetime import datetime, timedelta, timezone
from dateutil import tz, parser as dtparser
import feedparser, yaml
import argparse

# --- Filtros anti-lixo/duplicados de feeds genéricos ---
IGNORE_TITLE_PATTERNS = [
    r"^CoinDesk:\s*Bitcoin,\s*Ethereum,\s*Crypto News and Price Data$",
]
import re as _re
def _should_ignore(title: str, link: str) -> bool:
    t = (title or "").strip()
    for pat in IGNORE_TITLE_PATTERNS:
        if _re.search(pat, t, _re.IGNORECASE):
            return True
    # ignora páginas índice sem artigo
    if "coindesk.com" in (link or "") and "/live/" in link:
        return True
    return False

# --- Constantes de fuso/tempo ---
TZ_LISBON = tz.gettz("Europe/Lisbon")
NOW_UTC = datetime.now(timezone.utc)

# (Já não vamos usar janela fixa de 24h — fica aqui só se precisares noutro sítio)
# WINDOW_HOURS = 24

# --- Heurística de categorias ---
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
    # fallback por fonte
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

    # published date -> datetime UTC
    dt = None
    if "published_parsed" in entry and entry.get("published_parsed"):
        dt = datetime.fromtimestamp(time.mktime(entry["published_parsed"]), timezone.utc)
    elif "updated_parsed" in entry and entry.get("updated_parsed"):
        dt = datetime.fromtimestamp(time.mktime(entry["updated_parsed"]), timezone.utc)
    else:
        for key in ("published","updated","date"):
            if entry.get(key):
                try:
                    parsed = dtparser.parse(entry.get(key))
                    dt = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    pass
    if not dt:
        dt = NOW_UTC  # sem data: assume agora (raro em RSS decente)

    # id estável
    raw_id = entry.get("id") or link or title
    uid = hashlib.sha1((raw_id or "").encode("utf-8","ignore")).hexdigest()[:12]

    return {
        "id": uid,
        "time_iso": dt.astimezone(TZ_LISBON).isoformat(timespec="minutes"),
        "time": dt.astimezone(TZ_LISBON).strftime("%H:%M"),
        "category": None,  # preenchido depois
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
                # feedparser devolve objetos tipo dict-like; garantimos dict
                entry = {k: getattr(e, k) if hasattr(e, k) else e.get(k) for k in dir(e) if not k.startswith('_')} if hasattr(e, '__dict__') else e
                item, dt = normalize_item(entry, feed_title)
                out.append((item, dt, feed_title))
        except Exception as err:
            print("ERR feed", url, err)
    return out

def dedupe(items):
    seen = set()
    out = []
    for it in items:
        key = ((it["title"] or "")[:120].lower(), (it["source"] or "").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
    
def dedupe_keep_latest(items_with_dt):
    """
    items_with_dt: lista de tuples (item_dict, dt)
    Dedupe por (titulo, fonte) mantendo o dt mais recente.
    """
    best = {}
    for it, dt in items_with_dt:
        key = ((it.get("title") or "")[:120].lower(), (it.get("source") or "").lower())
        cur = best.get(key)
        if (cur is None) or (dt > cur[1]):
            best[key] = (it, dt)
    # devolve lista ordenada já fora
    return list(best.values())


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=2, help="nº de dias de histórico a incluir (default: 2)")
    return p.parse_args()

def main():
    # -------- NOVO: ler --days e construir cutoff por DIAS --------
    args = parse_args()
    lookback_days = max(1, args.days)

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=lookback_days)
    print(f"[fetch] usando lookback_days={lookback_days} (cutoff={cutoff_utc.isoformat()})")
    # ---------------------------------------------------------------

    # Lê config de feeds
    with open("config/feeds.yml","r",encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Junta todas as entradas de todas as listas em config
    all_entries = []
    # cfg pode ser dict com grupos -> listas
    if isinstance(cfg, dict):
        for group in cfg.values():
            if isinstance(group, list):
                all_entries.extend(collect_from(group))
    elif isinstance(cfg, list):
        all_entries.extend(collect_from(cfg))

    # Filtra lixo repetitivo (títulos genéricos) ANTES de montar items
filtered = [
    (it, dt, feed_title)
    for (it, dt, feed_title) in filtered
    if not _should_ignore(it.get("title"), it.get("url"))
]

# Enriquecer: categoria + tags simples
items_with_dt = []
for it, dt, feed_title in filtered:
    cat = guess_category(it["title"], it["summary"], it["source"] or feed_title)
    it["category"] = cat
    tags = []
    t = (it["title"] or "").lower()
    if "bitcoin" in t or "btc" in t: tags.append("BTC")
    if "ethereum" in t or "eth" in t: tags.append("ETH")
    it["tags"] = tags
    items_with_dt.append((it, dt))

    # Ordenar por mais recente
    items_with_dt.sort(key=lambda x: x[1], reverse=True)

    # Dedupe mantendo mais recente
    items_with_dt = dedupe_keep_latest(items_with_dt)

    # Cap para não inchar o ficheiro
    MAX_ITEMS = 400
    items_with_dt = items_with_dt[:MAX_ITEMS]


    # Agrupar por dia (Lisboa)
    days_map = {}
    for it, dt in items_with_dt:
        d_lis = dt.astimezone(TZ_LISBON).date().isoformat()
        days_map.setdefault(d_lis, {"date": d_lis, "attention_points": [], "items": []})
        it.pop("time_iso", None)  # remover helper
        days_map[d_lis]["items"].append(it)

    # Pontos de atenção: top 3 títulos do próprio dia
    for d in days_map.values():
    d["items"].sort(key=lambda x: x.get("time","00:00"), reverse=True)
    for h in d["items"]:
        title_clean = (h.get("title") or "").strip()
        if title_clean:
            d["attention_points"].append(title_clean[:120])
        if len(d["attention_points"]) >= 3:
            break

    # Ordena dias do mais recente para o mais antigo
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

