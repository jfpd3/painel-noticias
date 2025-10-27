#!/usr/bin/env python3
import os, json, time, hashlib, pathlib
import requests
import feedparser

# --- Config via Secrets/Env ---
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]        # github secret
CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]          # -100xxxxxxxxxxxx
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "6")) # limita posts por execução
HASHTAGS    = os.environ.get("HASHTAGS", "#BTC #Crypto #News")

ROOT    = pathlib.Path(__file__).resolve().parents[1]
FEEDS   = (ROOT / "feeds.txt").read_text().splitlines()
STATEFP = ROOT / "state.json"

def load_state():
    if STATEFP.exists():
        try:
            return json.loads(STATEFP.read_text())
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    STATEFP.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def item_id(entry):
    # GUID robusta
    cand = entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title") or str(entry)
    # normaliza
    return hashlib.sha1(cand.encode("utf-8", errors="ignore")).hexdigest()

def as_epoch(entry):
    # ordenação por data se existir
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        if entry.get(key):
            try:
                return int(time.mktime(entry[key]))
            except Exception:
                pass
    return 0

def format_msg(entry):
    title = entry.get("title", "").strip()
    link  = entry.get("link", "").strip()
    pub   = entry.get("published") or entry.get("updated") or ""
    parts = [
        f"📰 {title}" if title else "",
        f"🌍 {link}"  if link  else "",
        f"🕒 {pub}"   if pub   else "",
        HASHTAGS
    ]
    # sem parse_mode para evitar problemas com _[]* etc.
    return "\n".join([p for p in parts if p])

def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False
    }
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

def run():
    state = load_state()
    changed = False
    queued  = []

    for raw in FEEDS:
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        d = feedparser.parse(url)
        if d.bozo:
            print(f"[WARN] feed error: {url} -> {getattr(d, 'bozo_exception', '')}")
        seen = set(state.get(url, []))
        fresh = []
        for e in d.entries:
            iid = item_id(e)
            if iid not in seen:
                fresh.append((iid, e))
        # publica dos mais antigos para os mais novos (ordem natural)
        fresh.sort(key=lambda t: as_epoch(t[1]))
        for iid, e in fresh:
            queued.append((url, iid, e))

    # não spammar: cortamos ao MAX_PER_RUN
    queued = queued[-MAX_PER_RUN:]

    for url, iid, e in queued:
        msg = format_msg(e)
        send_to_telegram(msg)
        # update state
        lst = state.get(url, [])
        lst.append(iid)
        state[url] = lst[-200:]  # guarda só 200 por feed
        changed = True
        time.sleep(0.7)  # rate limit suave

    if changed:
        save_state(state)

if __name__ == "__main__":
    run()
