# OmniRoute висит на `free-stack` и `free-first` (разбор 2026-10-02)

Симптом: любой `claude -p` через `omniroute run claude` (то есть любой вызов `run_claude` /
`arun_claude`) не отвечает. В buy-smarter `--model haiku` упирается в 300 с, и `--model
free-first` упирается в 140 с даже на «say ok». Прямой `claude -p` без шлюза при этом
отвечает за 2–7 с. `omniroute health` сообщает `healthy`, по нему проблему не видно.

Данные: `~/.omniroute/call_logs/*/*.json` (последние 10 000 вызовов, 2026-09-27 … 2026-10-01
22:43 UTC), `/api/combos`, `/api/model-combo-mappings`, `/api/providers` (Bearer
`OMNIROUTE_API_KEY` из `~/.omniroute/.env`).

## Причины (по убыванию веса)

### 1. Подписка Claude в шлюзе мертва с 2026-09-23

`/api/providers` → connection `provider: claude`, `authType: oauth`:
`isActive: false`, `testStatus: expired`,
`lastError: "Refresh token consumed (unrecoverable_refresh_error). Please re-authenticate this account."`,
`lastErrorAt: 2026-09-23T20:47Z`.

Ни в одном из 10 000 вызовов в логе нет строки с `provider: claude`. Каждая ступень
`claude/...` в любом комбо пропускается молча. Для `sub-first`, `sub-first-opus` и `hard`
это первая ступень, поэтому они тоже сразу сваливаются на бесплатные цели.

Чинится только в OmniRoute: заново авторизовать Claude-подключение (дашборд → Providers →
claude → re-auth). Ретраи с нашей стороны тут не помогут.

### 2. Маппинги ведут haiku и sonnet мимо подписки

`/api/model-combo-mappings` (priority 100 сильнее priority 10):

| pattern | combo | где подписка |
|---|---|---|
| `claude-haiku-*` | `free-first` | 10-я, последняя ступень |
| `claude-sonnet-*` | `free-stack` (с 2026-09-08) | её нет совсем |
| `claude-opus-*` | `hard` | 1-я (но мертва, см. п.1) |
| `claude-*` | `sub-first` | 1-я, но перекрыта тремя правилами выше |

`--model sonnet` с 2026-09-08 физически не может попасть в подписку. Заметка о маппинге
от 2026-08-30 («sonnet → sub-first») устарела.

### 3. Бесплатные цели деградировали, каждая ступень стоит около 60 с

Вызовы с 2026-10-01T20 UTC, медиана длительности по цели:

| цель | статус | n | медиана |
|---|---|---|---|
| gemini/gemini-3.8-flash | 499 | 474 | 300 с (стрим до отмены клиентом) |
| gemini/gemini-3.8-flash | 504 | 343 | 87 с |
| kilocode/stepfun/step-3.7-flash:free | 504 | 216 | 60 с |
| gemini/gemini-3-flash-preview | 504 | 93 | 60 с |
| gemini/gemini-2.5-flash | 504 | 82 | 60 с |
| antigravity/claude-sonnet-4-6 | 504 | 78 | 125 с |
| kilocode/stepfun/step-3.7-flash:free | 200 | 36 | 28 с |

Тексты ошибок:

- gemini `[504]: Direct response did not start within 30000ms — retrying on a fresh socket`.
  Шлюз ждёт 30 с, повторяет и ждёт ещё 30 с. Отсюда ровно 60 с на ступень.
- gemini `[429]: You exceeded your current quota`. Это квота Google, а не шлюз.
- antigravity `[429]`, `[503] Pipeline gate rejected`.

В комбо по 9–10 ступеней по ~60 с. Цепочка проходится дольше таймаута клиента (300 с по
умолчанию у `run_claude`). Клиент рвёт соединение (`499 Request aborted`), пока роутер
застрял на 3–5-й ступени. Если до подписки дело и дойдёт, она стоит последней в
`free-first` и отсутствует в `free-stack`.

### 4. Ретраи Claude Code раздувают нагрузку, и квота не восстанавливается

- Почасовой объём вызовов (UTC): обычно 5–70 в час. 2026-10-01T21 — 1184, T22 — 1070.
- 149 разных тел запросов (верхнего уровня, `free-stack`) превратились в 515 запросов к шлюзу.
  Одно и то же тело повторяется до 9 раз: это встроенные ретраи `claude` на 5xx/обрыв.
- Каждый запрос верхнего уровня ещё и раскладывается на несколько ступеней. Получается
  около 2 200 обращений к провайдерам за 2 часа.
- Источник: параллельные субагентные прогоны той ночью (buy-smarter и другие проекты), плюс
  осиротевшие `claude -p`. В 02:55 их было около 10, возраст 45 минут, родитель `systemd --user`.
- Этот поток держит gemini в 429 и «cooling down». Новые вызовы снова попадают на
  тормозящие ступени. Петля самоподдерживается, пока нагрузка не спадёт.

### 5. Побочное: изолированные вызовы тащат пользовательские хуки

`--setting-sources user` подгружает user-level SessionStart-хуки. 1 671 запрос из окна
начинается с `SessionStart:startup hook success: PONYTAIL MODE ACTIVE …`, то есть каждый
«изолированный» вызов несёт лишний промпт плагина. На зависание это не влияет, но
добавляет токены и меняет поведение модели в «чистых» вызовах.

## Что делать

1. **Срочно:** re-auth Claude OAuth-подключения в OmniRoute. Проверка: `/api/providers` →
   `provider: claude` с `isActive: true` и `testStatus` не `expired`; в `call_logs`
   появляются строки `provider: claude` со статусом 200.
2. **Маппинг:** вернуть подписку в цепочку sonnet. Либо `claude-sonnet-*` → `sub-first`,
   либо добавить `claude/claude-sonnet-5` первой ступенью в `free-stack`. В `free-first`
   поднять подписку выше ступеней gemini, которые висят по 60 с, или хотя бы не держать её
   10-й.
3. **Таймауты ступеней:** 30 с на старт ответа × повтор — это слишком долго, когда ступеней 10.
   Нужен общий бюджет на комбо меньше таймаута клиента, либо убрать «retrying on a fresh
   socket» для бесплатных целей.
4. **Библиотека:**
   - Прямой режим (`direct=True`) мимо шлюза был добавлен и откачен (`216bc45`): правило —
     все вызовы только через `omniroute run`, комбо передавать можно. Значит, лечить надо
     шлюз (п.1–3), а не обходить его.
   - Короче первый таймаут при наличии fallback.
   - При таймауте `_kill_tree` уже убивает всё дерево. Осиротевшие процессы 2026-10-02
     остались от вызовов, где родителя убили снаружи (SIGKILL/timeout оболочки), и
     `_kill_tree` не успел сработать.
5. **Диагностика в следующий раз:** `health` не видит ни мёртвых подключений, ни медленных
   ступеней. Смотреть так:
   - `/api/providers`: `isActive` / `testStatus` / `lastError`;
   - агрегат `call_logs` по `(comboName, provider, model, status)` с медианой `duration`;
   - число одинаковых тел запросов, чтобы оценить ретрай-амплификацию.

Скрипт агрегата, который дал таблицы выше:

```python
import json, glob, os, collections, statistics
rows = []
for f in glob.glob(os.path.expanduser("~/.omniroute/call_logs/*/*.json")):
    try:
        rows.append(json.load(open(f))["summary"])
    except Exception:
        pass
d = collections.defaultdict(list)
for r in rows:
    if r["timestamp"] >= "2026-10-01T20":
        d[(r.get("comboName"), r.get("provider"), r.get("model"), r.get("status"))].append(r.get("duration") or 0)
for k, v in sorted(d.items(), key=lambda x: -len(x[1]))[:20]:
    print(len(v), int(statistics.median(v)), k)
```

## Дополнение: независимое воспроизведение из finance-insights (2026-10-01 ~22:54 UTC)

Отдельная проверка, по `omniroute logs --lines 400` (а не по `call_logs`). Картина та же.

**Прогон.** Три параллельных вызова, промпт «reply with ok», `timeout 60`:

```bash
echo "reply with ok" | NO_COLOR=1 timeout 60 omniroute run claude -- \
  -p --model <claude-sonnet-5-5|sub-first|free-first> \
  --output-format json --no-session-persistence --tools ""
```

Все три закончились `exit=124` через 61 с с пустым stdout.

**Шлюз, а не Claude Code.** Прямой `curl` на `POST /v1/messages` (`max_tokens: 16`) за 45 с не
получил ни байта: `http=000`, curl exit 28. Висит сам шлюз, и `claude-cli` с Claude Code тут
ни при чём.

**`sub-first` (не только `free-*`).** Подписка отпадает сразу, а следующая ступень молчит до
отмены клиентом:

```
22:53:51.350 [claude] All 1 connection(s) authentication expired — please reconnect in the dashboard
22:53:51.350 COMBO Provider claude auth failure (401) — marking for skip
22:54:48.265 Client disconnected (499) during antigravity/claude-sonnet-4-6 — stopping combo loop
```

**`free-first`.** Цепочка из соседнего запроса через тот же комбо:

```
22:52:41.283 [Antigravity] … Direct response did not start within 30000ms
22:53:11.628 Provider antigravity connection 448b8d2f… error (504) — marking for skip on remaining targets
22:54:11.688 Fetch timeout after 600000ms on …/gemini-3-flash-preview:streamGenerateContent?alt=sse
22:55:13.734 Fetch timeout after 600000ms on …/gemini-3-flash-preview (attempt 2/2)
```

Настроенный fetch timeout для Gemini равен 600 с. При клиентском таймауте 60–300 с до
следующей ступени после Gemini дело не доходит никогда.

**Обычный id.** `POST /v1/messages | claude-sonnet-5-5` сразу переходит в `comboName=free-stack`.
Это маппинг `claude-sonnet-*` из п.2, и Context-aware fallback оставил 3 из 9 целей:
`22:54:48.308 Client disconnected (499) during gemini/gemini-3.8-flash`.

**Что ещё сужает выбор:**
- Codex: `quota policy filtered 1 account(s): ba5569b6(session usage 100%)`.
- Antigravity: `quota-aware: 2 with quota, skipping 2 exhausted`.

### Последствия для потребителей

- **Модель по умолчанию в finance-insights никогда не доходит до подписки.**
  `finance_insights.provider.ClaudeCliProvider.DEFAULT_MODEL = "claude-sonnet-5-5"`, и через
  маппинг из п.2 этот id всегда уходит в `free-stack`. Даже после re-auth подписки (п.1) его
  не обслужит Claude, пока не поправлен маппинг. Другой путь — передавать комбо явно:
  `tier=`/`profile=` в `run_claude` или имя комбо в `--model`.
- **Тесты ходили в живой шлюз.** Когда `run_claude` перешёл на `subprocess.Popen`
  (`c31bde2`), моки `subprocess.run` в тестах finance-insights перестали его перехватывать.
  10 тестов слали реальные запросы и висели по 120 с, то есть сами добавляли нагрузку из
  п.4. Чинится в `finance-insights/tests/conftest.py`: любой `Popen` для
  `omniroute`/`claude` уходит в мок `run`, а без мока тест сразу падает. Тем же стоит
  закрыть тесты остальных потребителей `claude-cli`.

