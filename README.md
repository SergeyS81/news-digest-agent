# news-digest-agent

Автономный агент, который каждый час проверяет новости по заданным темам (через бесплатные RSS-ленты Google News + фильтр по ключевым словам) и присылает в Telegram только срочные/важные находки.

- `agent.py` — логика поиска, фильтрации, отправки и ведения истории.
- `.github/workflows/hourly.yml` — расписание (каждый час) через GitHub Actions, не зависит ни от какого локального устройства.
- `docs/` — статический дашборд (GitHub Pages) с историей запусков.

## Секреты (Settings → Secrets and variables → Actions)

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
