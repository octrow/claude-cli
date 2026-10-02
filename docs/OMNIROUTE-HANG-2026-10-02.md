# OmniRoute висит на `free-stack` и `free-first` (разбор 2026-10-02)

Симптом: любой `claude -p` через `omniroute run claude` (то есть любой вызов `run_claude` /
`arun_claude`) не отвечает. В buy-smarter `--model haiku` упирается в 300 с, и `--model
free-first` упирается в 140 с даже на «say ok». Прямой `claude -p` без шлюза при этом
отвечает за 2–7 с. `omniroute health` сообщает `healthy`, по нему проблему не видно.

См. также: [`INCIDENT-2026-10-02-omniroute-hang.md`](INCIDENT-2026-10-02-omniroute-hang.md), [`2026-10-02-combo-hangs.md`](2026-10-02-combo-hangs.md).

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
  осиротевшие `claude -p`. В 02:55 по местному времени (21:55 UTC) их было около 10, возраст 45 минут, родитель `systemd --user`.
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
  не обслужит Claude, пока не поправлен маппинг. Другой путь — передавать имя комбо прямо
  в `--model` (`model="sub-first"`). `tier=`/`profile=` комбо не выбирают, см. «Дополнение 2».
- **Тесты ходили в живой шлюз.** Когда `run_claude` перешёл на `subprocess.Popen`
  (`c31bde2`), моки `subprocess.run` в тестах finance-insights перестали его перехватывать.
  10 тестов слали реальные запросы и висели по 120 с, то есть сами добавляли нагрузку из
  п.4. Чинится в `finance-insights/tests/conftest.py`: любой `Popen` для
  `omniroute`/`claude` уходит в мок `run`, а без мока тест сразу падает. Тем же стоит
  закрыть тесты остальных потребителей `claude-cli`.

## Дополнение 2: из tiktok-lazy-follower (2026-10-01 ~23:00 UTC)

Причины те же, что выше. Ниже то, чего в этом разборе и в `INCIDENT-2026-10-02-omniroute-hang.md`
ещё нет. Первая находка опровергла прежний совет из «Последствий» выше (передавать комбо через
`tier=`/`profile=`), совет там уже исправлен.

### `tier=` / `profile=` не выбирают комбо

`build_args` превращает `tier`/`profile` в `omniroute run --profile <имя>`. У цели `claude` в
OmniRoute 3.8.50 этот флаг означает только `CLAUDE_CONFIG_DIR=~/.claude/profiles/<имя>`
(`bin/cli/commands/run.mjs:127`, `launch.mjs:190`), то есть отдельную директорию настроек
Claude Code. Маршрут он не трогает:

```bash
omniroute run --profile high claude --dry-run --json -- -p --model sonnet
# env.changedOrAdded: CLAUDE_CONFIG_DIR, ANTHROPIC_BASE_URL, … ; "model": null
```

- Комбо с именами `ultra`/`high`/`middle`/`low` в шлюзе нет. `omniroute combo list` выдаёт
  `sub-first`, `free-first`, `sub-first-opus`, `sonnet-wide`, `free-stack`, `hard`.
- Таблица `tier_config` в `storage.sqlite` описывает ценовые уровни провайдеров (free/cheap),
  а не комбо.
- Раздел «Четыре тира комбо» в `OMNIROUTE.md` описывал несуществующий механизм (исправлен в «Как выбирается комбо»).
  `tier="high"` создаёт пустой `~/.claude/profiles/high`, а запрос уходит по тем же `model_combo_mappings`.
- Комбо выбирается только моделью: имя комбо прямо в Claude-side `--model`. Проверено:
  `--model sub-first` дошёл до шлюза (`📥 POST /v1/messages | sub-first`) и ответил.
- `profile="free-tier"` в chat-watch отвечал быстрее не из-за маршрута. Пустой
  `CLAUDE_CONFIG_DIR` не грузит `~/.claude` (CLAUDE.md, rules, хуки, плагины), поэтому запрос
  легче, а маршрут тот же.
- Следствие: исправление `tier` в библиотеке — это `--model <combo>` после `--`.
  `--profile` стоит оставить только для изоляции настроек. Пока это не сделано, `tier=` у
  потребителей (в tlf это `digest.tier`) ничего не меняет.

### Живой замер на «ответь ok» (`CLAUDE_CODE_MAX_RETRIES=1`, ~22:59 UTC, уже после прогрева DNS)

| вызов | итог | TTFT | путь по `app.log` |
|---|---|---|---|
| `--model sonnet` (`free-stack`) | ok | 27 с | gemini-3.8-flash: 429 за 5 с (`cooling down 25s`), затем `kilocode/stepfun/step-3.7-flash:free` 200 за 21 с |
| `--model sub-first` | ok | 77 с | claude: 401 skip, antigravity: 429 через 47 с, повтор Claude Code, antigravity: 429 через 23 с, затем gemini-3-flash-preview 200 за 6 с |

Даже в спокойный момент голова `sub-first` после мёртвой подписки стоит до 47 с на один отказ:
antigravity отдаёт 429 медленно.

### Мелочи, найденные по пути

- **Claude Code сам повторяет до ~10 раз, а `run_claude` ему не мешает.** Окружение процесса не
  трогается, `CLAUDE_CODE_MAX_RETRIES` не выставлен. В шторм 2271 запись `call_logs`
  (2026-10-01 21–23 UTC) пришлась на 215 уникальных последних сообщений: медиана 5 повторов,
  максимум 95. Во время самого сбоя ограничение повторов ответа не вернёт (см. INCIDENT), но
  умножение нагрузки и время отказа оно режет. За fallback и так отвечает комбо шлюза.
- **Фильтр совместимости отсекает до 6 из 9 целей.** Claude Code шлёт `max_tokens`
  64000/128000 с `thinking` и `output_config`. `filterTargetsByRequestCompatibility` проверяет
  tools, vision, structured output, context window и output tokens. В `app.log`
  `kept 5/9` — 4789 раз, `kept 3/9` — 402 раза. Гипотеза, не проверена: виноваты
  `max_tokens`/`output_config`. Проверить можно через `CLAUDE_CODE_MAX_OUTPUT_TOKENS`.
- **Профиль `~/.claude/profiles/Reply with the single word: ok`** (создан 2026-09-22). Промпт
  стоял до `--`, и `omniroute run` прочитал его как собственный `-p` (алиас `--profile`).
  `build_args` от этого защищён, ручные вызовы `omniroute run claude -p "…"` без `--` — нет.
- **`omniroute` грузит `.env` из текущей директории.** При запуске из каталога проекта
  выводится `Loaded env from /home/octrow/dev/tiktok-lazy-follower/.env`, и секреты проекта
  уходят в окружение шлюза и Claude Code. `run_claude` работает в одноразовой директории, так
  что это касается только ручных и диагностических вызовов.
- **Подписка мертва с точностью до часа:** последний успех
  `Model claude/claude-sonnet-5 succeeded (1735ms)` — 2026-09-23 08:15 UTC; первый
  `authentication expired` — 2026-09-23 21:40 UTC (`app.2026-09-22_234825.log`).
