import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
SENT_LOG_PATH = os.path.join(ROOT, "sent_log.json")
HISTORY_PATH = os.path.join(ROOT, "docs", "digest_history.json")

SENT_LOG_MAX_AGE_DAYS = 3
HISTORY_MAX_AGE_DAYS = 30
LOOKBACK_MINUTES = 90  # slightly over an hour to cover cron jitter

TOPICS = [
    {
        "name": "Здравоохранение и прорывные мед. технологии",
        "emoji": "🧬",
        "queries": [
            ("прорыв медицина технологии лечение", "ru"),
            ("medical breakthrough FDA approval", "en"),
        ],
        "always_urgent": False,
        "keywords": [
            "прорыв", "впервые в мире", "одобрил fda", "одобрила fda", "fda approved",
            "клинические испытания успешно", "новый метод лечения", "вылечили",
            "breakthrough", "first in the world", "approved by fda",
        ],
    },
    {
        "name": "Здравоохранение Татарстана / РКБ",
        "emoji": "🏥",
        "queries": [
            ("РКБ Татарстан", "ru"),
            ("Республиканская клиническая больница Татарстан", "ru"),
        ],
        "always_urgent": True,
        "keywords": [],
    },
    {
        "name": "Значимые новости рынков",
        "emoji": "📈",
        "queries": [
            ("рынки биржа акции курс рубля", "ru"),
        ],
        "always_urgent": False,
        "keywords": [
            "обвал", "рекорд", "рухнул", "рухнула", "рухнули", "повысил ставку",
            "понизил ставку", "кризис", "паника", "остановлены торги", "рекордный рост",
            "рекордное падение",
        ],
    },
    {
        "name": "Мировая политика и СВО",
        "emoji": "🕊️",
        "queries": [
            ("СВО Украина", "ru"),
            ("мировая политика переговоры", "ru"),
        ],
        "always_urgent": False,
        "keywords": [
            "перемирие", "эскалация", "удар по", "переговоры", "соглашение",
            "мобилизация", "ультиматум", "прорыв обороны", "ceasefire", "escalation",
        ],
    },
    {
        "name": "Технологии искусственного интеллекта",
        "emoji": "🤖",
        "queries": [
            ("искусственный интеллект новости", "ru"),
            ("OpenAI OR Anthropic OR \"Google DeepMind\" release", "en"),
        ],
        "always_urgent": False,
        "keywords": [
            "выпустил", "выпустила", "релиз", "запрет", "регулирование", "прорыв",
            "утечка данных", "взлом", "release", "launch", "banned", "regulation",
            "breakthrough",
        ],
    },
]

# Trusted regional outlets checked explicitly per topic (in addition to general
# Google News search), since Google's default ranking can under-surface them.
PRIORITY_SITES = ["tatar-inform.ru", "rt-online.ru", "intertat.tatar", "business-gazeta.ru"]
PRIORITY_SITE_FILTER = "(" + " OR ".join(f"site:{s}" for s in PRIORITY_SITES) + ")"

for _topic in TOPICS:
    _terms = "РКБ OR больница OR Минздрав Татарстан" if _topic["always_urgent"] else " OR ".join(_topic["keywords"][:6])
    _topic["queries"].append((f"{PRIORITY_SITE_FILTER} {_terms}", "ru"))


def google_news_rss_url(query, lang):
    q = urllib.parse.quote(query)
    if lang == "ru":
        return f"https://news.google.com/rss/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def entry_id(link, title):
    return hashlib.sha256((link or title).encode("utf-8")).hexdigest()[:16]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def prune_by_age(items, date_key, max_age_days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept = []
    for item in items:
        try:
            dt = datetime.fromisoformat(item[date_key])
        except (KeyError, ValueError):
            continue
        if dt >= cutoff:
            kept.append(item)
    return kept


def fetch_recent_entries(query, lang):
    url = google_news_rss_url(query, lang)
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[warn] failed to fetch feed for '{query}' ({lang}): {exc}", file=sys.stderr)
        return []

    parsed = feedparser.parse(resp.content)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)
    recent = []
    for e in parsed.entries:
        published = e.get("published_parsed")
        if not published:
            continue
        pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
        if pub_dt < cutoff:
            continue
        recent.append({
            "title": e.get("title", "").strip(),
            "link": e.get("link", "").strip(),
            "published_at": pub_dt.isoformat(),
        })
    return recent


def matches_keywords(text, keywords):
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


def collect_urgent_items(sent_ids):
    found = []
    for topic in TOPICS:
        for query, lang in topic["queries"]:
            for entry in fetch_recent_entries(query, lang):
                eid = entry_id(entry["link"], entry["title"])
                if eid in sent_ids:
                    continue
                if not topic["always_urgent"] and not matches_keywords(entry["title"], topic["keywords"]):
                    continue
                found.append({
                    "id": eid,
                    "topic": topic["name"],
                    "emoji": topic["emoji"],
                    "title": entry["title"],
                    "link": entry["link"],
                })
    # de-dup within this run (same story from multiple queries)
    seen = set()
    unique = []
    for item in found:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique


def format_message(items):
    lines = ["🚨 Важные новости", ""]
    for item in items:
        lines.append(f"{item['emoji']} {item['topic']}")
        lines.append(item["title"])
        lines.append(item["link"])
        lines.append("")
    text = "\n".join(lines).strip()
    return text[:3900]


def send_telegram_message(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def main():
    now_iso = datetime.now(timezone.utc).isoformat()

    sent_log = load_json(SENT_LOG_PATH, [])
    sent_ids = {item["id"] for item in sent_log}

    history = load_json(HISTORY_PATH, [])

    items = collect_urgent_items(sent_ids)

    if not items:
        print("No urgent news this run.")
        history.append({"run_at": now_iso, "sent": False, "items": []})
        history = prune_by_age(history, "run_at", HISTORY_MAX_AGE_DAYS)
        save_json(HISTORY_PATH, history)
        return

    message = format_message(items)
    send_telegram_message(message)
    print(f"Sent {len(items)} item(s) to Telegram.")

    for item in items:
        sent_log.append({"id": item["id"], "title": item["title"], "sent_at": now_iso})
    sent_log = prune_by_age(sent_log, "sent_at", SENT_LOG_MAX_AGE_DAYS)
    save_json(SENT_LOG_PATH, sent_log)

    history.append({
        "run_at": now_iso,
        "sent": True,
        "items": [{"topic": i["topic"], "title": i["title"], "link": i["link"]} for i in items],
    })
    history = prune_by_age(history, "run_at", HISTORY_MAX_AGE_DAYS)
    save_json(HISTORY_PATH, history)


if __name__ == "__main__":
    main()
