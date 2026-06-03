# tools/ — вспомогательные скрипты вики

## Сквозной поток обновления данных

```
tankz-tg-knowledge (приложение)          tank500-wiki
┌──────────────────────────┐
│ python sync.py --run-once │  тянет Telegram → data/<topic>/YYYY-MM.md (+ media/)
└────────────┬─────────────┘
             │  pull (rsync-подобное зеркало)
             ▼
┌────────────────────────────────────────────────────────────┐
│ python3 tools/sync_from_app.py                             │
│   1. зеркалит data/ → raw/tankz-club/<topic>/              │
│   2. filter_t500.py по СМЕШАННЫМ топикам → <topic>-t500/   │
│   3. пишет манифест изменений → tools/.changed-months      │
└────────────┬───────────────────────────────────────────────┘
             │  манифест NEW/GREW
             ▼
   в сессии Claude: skill wiki-ingest («разбери изменения»)
   → разбирает цели, обновляет вики, чистит манифест
```

## Скрипты

### `sync_from_app.py` — синхронизация приложение → raw

Единственный санкционированный писатель `raw/tankz-club/` (вместе с `filter_t500.py`).
Приложение остаётся чистым продюсером и ничего не знает про вики.

```bash
# первый запуск при развёртывании — засеять состояние тем, что уже в raw (всё уже разобрано),
# чтобы не пометить весь архив как новый:
python3 tools/sync_from_app.py --baseline

# обычный прогон (после того как приложение обновило data/):
python3 tools/sync_from_app.py

# всё за один раз (сам дёрнет sync.py --run-once в приложении):
python3 tools/sync_from_app.py --pull-app

# посмотреть план, ничего не писать:
python3 tools/sync_from_app.py --dry-run
```

**Карта топиков** (в начале скрипта, `TOPIC_POLICY`):
- *dedicated* (`tank-500`, `service-campaign-tank-300-500`) — зеркалятся as-is + media; ingest читает
  raw-месяц напрямую, фильтр не нужен.
- *mixed* (`general`, `tech-questions`, `wheels-discs-tyres`, `suspension-chassis`,
  `questions-and-answers`) — зеркалятся, затем `filter_t500.py` → `<topic>-t500/`; ingest читает
  `-t500`-месяц. У `questions-and-answers` — `--loose`.
- Источник из `config.yaml` приложения, которого нет в карте → warn + skip (классифицировать вручную).

**Манифест `tools/.changed-months`** — цели для ingest, по строке: `<dir>/<YYYY-MM>.md  NEW|GREW`.
`NEW` — новый месяц; `GREW` — в уже разобранный месяц дописались сообщения (дельта-разбор с дедупликацией).
Пустые отфильтрованные месяцы (0 T500-тредов) в манифест не попадают. Skill `wiki-ingest` расходует
манифест и чистит разобранные строки.

**Состояние `tools/.sync-state.json`** — sha256 каждого месячного файла-цели; по нему различаются
NEW/GREW и обеспечивается идемпотентность (повторный прогон без новых данных → пустой манифест).

### `filter_t500.py` — извлечение T500-тредов

Оставляет из месячного архива только треды с маркерами Tank 500. Обычно вызывается автоматически из
`sync_from_app.py`; вручную — для разовой перегенерации:

```bash
python3 tools/filter_t500.py raw/tankz-club/<topic>/ --out raw/tankz-club/<topic>-t500/ [--loose]
```
