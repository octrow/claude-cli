# 2026-10-02: почему `free-stack` и `hard` «висят»

Разбор ночи 2026-10-02, 02:04–03:16 по местному времени (UTC+5), то есть 2026-10-01
21:04–22:16 UTC. Все времена ниже даны в **UTC**, как в журналах OmniRoute.

См. также: [`INCIDENT-2026-10-02-omniroute-hang.md`](INCIDENT-2026-10-02-omniroute-hang.md), [`OMNIROUTE-HANG-2026-10-02.md`](OMNIROUTE-HANG-2026-10-02.md).

Как разбирали: журналы systemd, `jobs/data/logs/{pipeline,cost}.jsonl`, таблица
`call_logs` и `combos` в `~/.omniroute/storage.sqlite` (открыта `sqlite3 -readonly`),
`~/.omniroute/logs/application/app.log`, `omniroute resilience status|lockouts`,
`omniroute health|quota` и исходники `omniroute 3.8.50` в
`~/.nvm/versions/node/v22.23.2/lib/node_modules/omniroute/`. **Ни одного запроса к модели не
отправлено.** Сетевые пробы делались только `curl` на корень API провайдеров, без
авторизации и без расхода квоты.

## Коротко

«Зависание» складывается из трёх причин, и каждая из них по отдельности уже ломает вызов.

1. **Сеть хоста в ту ночь работала с перебоями. Провайдеры тут ни при чём.** С 18:00 UTC
   плановые `connection-test` OmniRoute к ~30 разным провайдерам (gemini, groq, mistral,
   openrouter, huggingface, moonshot…) упирались в таймауты 8 с. В 21:37 и 22:02 NetworkManager
   переходил в `CONNECTED_SITE`, то есть терял интернет. С 21:52 до 22:11 у chat-watch шли
   `Temporary failure in name resolution`. Каждая попытка к gemini и kilocode кончалась так:
   `Direct response did not start within 30000ms — retrying on a fresh socket`, потом ещё
   30 с на свежем сокете и 504. Таких строк в `app.log` за ночь 904.
2. **Бюджет времени OmniRoute на один таргет больше, чем таймаут вызывающего.** Фактический
   per-target timeout у обоих комбо равен **310 с**, а не 120 с по умолчанию: включён
   `comboCooldownWait` с `budgetMs=300000`, и он поднимает нижнюю границу до budget + 10 с.
   Добавьте к этому 4 попытки на таргет у `free-stack` (`maxRetries: 3`) и ротацию по 4
   аккаунтам antigravity у `hard`. Rank убивает `claude` через 120 с, черновик через 600 с.
   В итоге комбо **почти никогда не доходит до второго таргета**: из 352 запросов с пятью
   таргетами в `free-stack` дальше первого (gemini) ушли 7.
3. **Первые таргеты `hard` мертвы, и это не связано с ночью.** Подключение `claude`
   в статусе `expired` с 2026-09-23 20:47 UTC: `Refresh token consumed
   (unrecoverable_refresh_error)`. Отсюда 401 «authentication expired» на
   `claude/claude-opus-5` и `claude/claude-sonnet-5`, а также «provider claude marked
   exhausted this request (#1731)». Codex отфильтрован политикой квоты (`session usage 99%`).
   У antigravity `claude-sonnet-4-6` на всех четырёх аккаунтах `quota_exhausted`, у
   `opus-4-6-thinking` осталось около 3 %. Так что `hard` начинает работу сразу с хвоста,
   а хвост в ту ночь не отвечал из-за сети.

Что из этого чьё:

| Слой | Что именно |
|---|---|
| Upstream / сеть | Обрывы связи хоста (Wi-Fi/ISP), 429 у ключа gemini (паузы по 60 с с 21:08 до 21:19) |
| Поведение OmniRoute | Per-target floor 310 с, повтор того же таргета ×4, ротация аккаунтов, общий для `antigravity/*` и `agy/*`, неограниченная локальная очередь у API-key провайдеров, `STREAM_IDLE`/`FETCH` по 600 с, `omniroute health` пишет «healthy» |
| Наши настройки | Rank с таймаутом 120 с и повтором (2 × 120 = 240 с на строку), без `CLAUDE_CODE_MAX_RETRIES`; разрыв цепочки после 5 неудач подряд не сработал; подключение `claude` не переавторизовано 9 дней |

## 1. Состав комбо (на 2026-10-02, из таблицы `combos`)

Обе стратегии `priority`. Маппинги (`model_combo_mappings`): `claude-sonnet-*` → `free-stack`,
`claude-opus-*` → `hard`, `claude-haiku-*` → `free-first`, остальные `claude-*` → `sub-first`.
Значит, в `free-stack` попадает **любой** клиент с `--model sonnet`, а не только rank.

### free-stack (v2, обновлён 2026-09-17)

| # | Таргет |
|---|---|
| 1 | `gemini/gemini-3.8-flash` |
| 2 | `kilocode/stepfun/step-3.7-flash:free` |
| 3 | `kilocode/poolside/laguna-s-2.1:free` |
| 4 | `gemini/gemini-3-flash-preview` |
| 5 | `kilocode/openrouter/free` |
| 6 | `cloudflare-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast` |
| 7 | `cohere/command-a-03-2025` |
| 8 | `groq/openai/gpt-oss-120b` |
| 9 | `huggingface/deepseek-ai/DeepSeek-V3` |
| 10 | `openrouter/nex-agi/nex-n2.5-pro:free` |

Конфиг: `maxRetries: 3`, `retryDelayMs: 500`, `context_cache_protection: true`.
`targetTimeoutMs` и `comboTimeoutMs` не заданы.

Как это выглядело на деле. Резолвер видит 9 из 10 таргетов конфига, после него «Context-aware fallback»
оставляет 3 или 5 из 9 (`kept 3/9` 145 раз, `kept 5/9` 356 раз): таргеты с малым окном
контекста не берут запрос Claude Code. Для `kept 3/9` остаются `gemini-3.8-flash` →
`stepfun` → `nex-n2.5-pro`, причём последнего «is not available in the active live catalog».
**Получается, что у rank-запроса жизнеспособных таргетов было два, и оба шли через сеть,
которая в ту ночь отваливалась.**

### hard (v2, обновлён 2026-09-23)

| # | Таргет | Состояние ночью |
|---|---|---|
| 1 | `claude/claude-opus-5` | подключение `expired`, мгновенный отказ |
| 2 | `codex/gpt-5.6-terra` | отфильтрован, `session usage 99%` |
| 3 | `claude/claude-sonnet-5` | пропущен, «provider claude marked exhausted this request» |
| 4 | `antigravity/claude-opus-4-6-thinking-medium` | 4 аккаунта, у каждого 2×30 с до 504 |
| 5 | `agy/claude-opus-4-6-thinking-medium` | **алиас на те же аккаунты antigravity** (`agy/… → antigravity/…`) |
| 6 | `antigravity/claude-sonnet-4-6` | `quota_exhausted` на всех аккаунтах |
| 7 | `agy/claude-sonnet-4-6` | то же |
| 8 | `gemini/gemini-3.8-flash` | 504 / 429 |
| 9 | `grok-cli/grok-4.7` | 504 через 60 с |

Конфиг: `{}`, то есть берутся дефолты: `maxRetries: 1`, `retryDelayMs: 2000`.

### Глобальные настройки, которые решают тайминг

Источники: `omniroute resilience status`, `~/.omniroute/.env`, окружение процесса сервера
и исходники 3.8.50.

| Настройка | Значение | Откуда | Что значит |
|---|---|---|---|
| per-target timeout | **310 с** | `DEFAULT_COMBO_TARGET_TIMEOUT_MS=120000`, но `comboCooldownWait.enabled` поднимает его до `budgetMs(300000)+10000` (`open-sse/services/comboConfig.ts`) | Максимальные длительности gemini в `call_logs` 308–311 с, это совпадает |
| `comboCooldownWait` | `enabled, maxWaitMs 90000, maxAttempts 5, budgetMs 300000` | resilience | Таргет может до 5 минут ждать конца кулдауна |
| Ожидание начала ответа | 30 с + повтор на свежем сокете 30 с | `OMNIROUTE_DIRECT_HEADERS_TIMEOUT_MS` (не задан, по умолчанию 30000) | Одна попытка к молчащему хосту стоит около 60 с |
| `FETCH_TIMEOUT_MS`, `STREAM_IDLE_TIMEOUT_MS` | 600 с | дефолт | Таргет, который отдал заголовки и замолчал, держит запрос 10 минут |
| Предохранитель цикла комбо | 600 с | `COMBO_LOOP_SAFETY_TIMEOUT_MS` (используется, когда `comboTimeoutMs` не задан) | 92 раза за ночь «Combo loop safety timeout (600000ms) … force-terminating» |
| Локальная очередь API-key провайдеров | `concurrentRequests 6`, 60 rpm, `maxQueueDepth 0` (без ограничения), `maxWaitMs 600000` | `RATE_LIMIT_MAX_WAIT_MS=600000` в `~/.omniroute/.env` | У gemini и kilocode один ключ на всех клиентов, остальные ждут в очереди. По коду ожидание в очереди `maxWaitMs` **не ограничивает** |
| `waitForCooldown` | `maxRetries 3, maxRetryWaitSec 30` | resilience | |

## 2. Хронология ночи

### rank → sonnet → free-stack (`jobhunt-analyze`, run `659c1fae0635`)

| UTC | Что происходило |
|---|---|
| 21:04 | Старт, 68 строк на модель, 4 воркера, таймаут 120 с |
| 21:07–21:16 | 18 успешных вызовов (15–234 с), почти все через `kilocode/stepfun`, gemini один раз. Ключ gemini получает 429 и уходит в паузу на 60 с («dropping N queued request(s)») |
| с 21:16 | **Ни одного успеха до самого конца.** Каждая попытка gemini и stepfun кончается 504 через ~60 с: две паузы по 30 с без начала ответа |
| 21:20–22:00 | Каждые 4 минуты падают ровно 4 строки, у каждой `ms ≈ 240 000`: `ClaudeCliProvider` даёт вызову 2 попытки по 120 с |
| 21:37, 22:02 | NetworkManager: `CONNECTED_SITE`, интернета нет |
| 22:04 | systemd `TimeoutStartSec=3600` убивает сервис, итог 53 из 68 FAILED |

Куда ушло время одного вызова rank (120 с) внутри OmniRoute:

```
gemini-3.8-flash, попытка 1:  30 с без начала ответа + 30 с на свежем сокете → 504   (~60 с)
                  попытка 2:  то же самое                                          (~60 с)
                  ── здесь rank убивает claude (120 с) → в журнале 499 «Client disconnected
                     during gemini/gemini-3.8-flash — stopping combo loop» (402 раза)
                  попытки 3–4 и stepfun так и не начались
```

Если бы rank ждал дольше: gemini ×4 по ~60 с (плюс очередь и ожидание кулдауна,
ограниченные 310 с) ≈ 4–5 минут, потом stepfun ×4 по ~60 с ≈ 4 минуты, потом `nex`, которого
нет в каталоге. При такой сети вызов не завершился бы ни при каком разумном таймауте.

Нагрузка шла не только от rank. С 21:20 по 22:05 в `free-stack` пришло 449 запросов из 316
разных разговоров (`session_tag`), а rank за то же время сделал около 50 вызовов. 219
запросов комбо оборвались с 499 **ровно на 300 с**: это другой клиент с дедлайном 300 с
(это значение по умолчанию у `run_claude(timeout=300)`; кто именно, по журналам не
установить). Все они делили один ключ gemini с лимитом 6 одновременных запросов. Когда
upstream молчит, каждый запрос держит слот по 60 с и больше, и очередь растёт. Отсюда
средние 171–300 с на попытку gemini.

### draft → opus → hard (`jobhunt-draft`, run `53920ff5e40f`)

Трасса первого вызова (Inetum, review, 21:31:29 UTC) из `app.log`:

| UTC | Шаг |
|---|---|
| 21:31:30.01 | `claude \| 1 accounts found but none active` → таргет 1 отказывает за 2 мс |
| 21:31:30.02 | `claude/claude-sonnet-5` пропущен (#1731), codex отфильтрован (99 %) |
| 21:31:30 → 21:35:46 | `antigravity/claude-opus-4-6-thinking-medium`: 4 аккаунта по очереди, на каждом `daily-cloudcode-pa` 30 с + `cloudcode-pa` 30 с → 504 и блокировка на 5 с. **4 мин 16 с на один таргет** |
| 21:35:46 → … | `agy/claude-opus-4-6-thinking-medium` → `antigravity/…`, те же 4 аккаунта и те же 60 с на каждом |
| 21:41:27 | Клиент (600 с) обрывает вызов. До `antigravity/sonnet-4-6`, gemini и grok очередь не дошла |

Дальше сработал наш собственный перебор в `apply_batch`. Он прибил запрос к
`antigravity/claude-opus-4-6-thinking-medium`, и тот провисел ещё 600 с (21:41–21:51), потом
`claude/claude-opus-5` за 3 с вернул **401 «All 1 connection(s) authentication expired»**.
Второй вакансии (Windsor.ai) досталась та же схема: 600 с без ответа, codex `usage_limit` за
44 с, `claude` 401. Третья (Devoteam) повисла так же. Все ошибки 29.09 («Inetum 502 auth»,
«All credentials cooling down») тянутся от того же протухшего подключения `claude`.

## 3. Корневые причины

1. **Сеть хоста (upstream).** Без неё ночь прошла бы обычно. Признаки: одновременные
   таймауты к десяткам несвязанных провайдеров, `CONNECTED_SITE`, ошибки DNS у соседнего
   сервиса. Во время разбора (около 22:55 UTC) 3 из 12 проб к `api.kilo.ai` висели 5 с, а
   при первой пробе `api.anthropic.com`, `api.mistral.ai`, `api.kilo.ai` и `www.google.com`
   одновременно молчали 15 с. Потерь `ping` до шлюза и до 8.8.8.8 при этом не было: обрывы
   короткие.
2. **OmniRoute тратит время на мёртвый таргет дольше, чем вызывающий готов ждать.** Floor
   в 310 с из-за `comboCooldownWait`, `maxRetries: 3` у `free-stack` (повтор того же
   молчащего хоста), по 60 с на попытку из-за свежего сокета, ротация по 4 аккаунтам
   antigravity, и `agy/*` снова идёт в те же аккаунты. Комбо задумывалось как «упасть
   быстро и перейти к следующему», а на деле застревает на первом живом по конфигу таргете.
3. **Подключение `claude` протухло 9 дней назад.** У `hard` нет быстрого первого таргета,
   и каждый вызов opus сразу падает в цепочку antigravity, где квота почти кончилась.
4. **Настройки вызывающего.** Rank: 120 с × 2 попытки, `CLAUDE_CODE_MAX_RETRIES` не задан
   (у `apply_batch` стоит 2), а разрыв после 5 неудач подряд (`RANK_FAILURE_STREAK_LIMIT=5`)
   не остановил прогон после 28+ неудач. Почему, нужно смотреть отдельно в `jobs`, этот
   разбор код не трогал.

Чего здесь **не** было: `ALL_TARGETS_SKIPPED` и ошибок конфигурации комбо. 503 у gemini
были настоящими перегрузками и 429, а не нехваткой квоты.

## 4. Рекомендации по порядку пользы

Состав комбо не меняется. Всё ниже касается таймаутов, повторов и состояния подключений.

**Применено 2026-10-03 (пункты 2–3):** `targetTimeoutMs`/`comboTimeoutMs` = 40/110 с у
`free-stack` и `free-first`, 90/280 с у `hard` и `sub-first-opus`. `comboTimeoutMs` взят чуть
ниже самого короткого таймаута вызывающего: rank 120 с, chat-watch 120 с, buy-smarter opus
300 с. Пункты 1 и 4–8 не применены.

1. **Переавторизовать подключение `claude`** (Dashboard → Providers → claude → reconnect).
   К настройкам это не относится, но это первое по пользе. *Эффект:* у `hard` снова есть
   быстрый первый таргет, opus перестаёт уходить в antigravity, где квоты около 0 %, а
   ночные 401 и «exhausted this request» пропадают. Проверка:
   `sqlite3 -readonly ~/.omniroute/storage.sqlite "select provider,is_active,test_status from provider_connections where provider='claude'"`
   должно показать `active`.
2. **Задать каждому комбо `comboTimeoutMs` чуть меньше таймаута вызывающего:**
   `free-stack` около 110 000, `hard` около 570 000. *Эффект:* комбо успевает вернуть свой
   504 со сводкой по всем таргетам **до** того, как клиент его убьёт. Вместо немого «timed
   out after 120s» в ошибке будет причина, а OmniRoute сам перестанет запускать новые
   попытки (`Combo global timeout … after N/M targets`).
3. **Задать `targetTimeoutMs` явно:** `free-stack` 30 000–45 000, `hard` 90 000.
   *Эффект:* явное значение перекрывает floor в 310 с от `comboCooldownWait`. Для стриминга
   оно ограничивает только время до заголовков, саму генерацию не обрезает (см.
   комментарий к `DEFAULT_COMBO_TARGET_TIMEOUT_MS`). Молчащий таргет отпускается за
   секунды, а не за минуты.
4. **Снизить `maxRetries` у `free-stack` с 3 до 0–1 и у `hard` с 1 (дефолт) до 0.**
   *Эффект:* на 504 из-за таймаута начала ответа комбо не повторяет тот же хост ещё три
   раза по 60 с, а сразу идёт к следующему таргету. Повторы нужны против кратких 429, а с
   ними и так справляется `waitForCooldown`.
5. **Ужать `comboCooldownWait.budgetMs` с 300 000 до примерно 30 000** (`omniroute
   resilience config set …`) или выключить его для этих комбо. *Эффект:* исчезает сам
   источник floor в 310 с, и ни один таргет больше не ждёт кулдауна дольше таймаута rank.
6. **`OMNIROUTE_DIRECT_HEADERS_TIMEOUT_MS=15000`** в `~/.omniroute/.env`. *Эффект:* попытка к
   молчащему хосту стоит около 30 с вместо 60. Риск: у reasoning-моделей без раннего
   стрима заголовки могут приходить позже 15 с. Если в `app.log` появятся ложные 504 у
   `antigravity/*-thinking`, значение надо вернуть.
7. **`STREAM_IDLE_TIMEOUT_MS=180000`** (и `FETCH_BODY_TIMEOUT_MS` такой же). *Эффект:*
   таргет, который отдал заголовки и замолчал, отпускается через 3 минуты, а не через 10.
   За ночь 43 запроса `free-stack` закончились 504 ровно на 600 с.
8. **`RATE_LIMIT_MAX_QUEUE_DEPTH=8`** и `RATE_LIMIT_MAX_WAIT_MS=120000` вместо 600000.
   *Эффект:* когда единственный ключ gemini или kilocode забит, новые запросы уходят к
   следующему таргету, а не копятся в очереди без ограничения.
9. **На стороне claude-cli / jobs** (это код, сейчас его не трогаем, только запись):
   - выставлять `CLAUDE_CODE_MAX_RETRIES` и для rank, как уже делает `apply_batch`;
   - не повторять вызов после `timed out` внутри одной строки rank: второй раз в ту же
     сеть даёт те же 120 с;
   - перед прогоном и после N неудач подряд проверять сеть (`nmcli -t -f STATE general` =
     `connected`) и последние 5 минут комбо в `call_logs`. При сплошных 499/504 откладывать
     прогон, а не жечь час;
   - выяснить, почему не сработал `RANK_FAILURE_STREAK_LIMIT=5`.

Пункты 2–5 правят `config` у комбо или resilience-настройки, состав комбо при этом не
меняется. Если владелец считает, что «комбо не трогать» распространяется и на `config`,
остаются пункты 1 и 5–9.

## 5. Диагностика за 5 минут

`omniroute health` в ту ночь отвечал `healthy`, а `omniroute quota` отвечал `No quota
information available`. Обоим верить нельзя. `omniroute logs --combo free-stack` на этой
установке ничего не вернул, поэтому надёжный путь идёт через read-only SQLite и `app.log`.

```bash
DB=~/.omniroute/storage.sqlite; LOG=~/.omniroute/logs/application/app.log

# 1. Что вызывающие видели сами (≈10 с)
journalctl --user -u jobhunt-analyze -u jobhunt-draft --since -2h -o cat \
  | grep -E 'timed out|FAILED|failed:|401|usage_limit' | tail -20

# 2. Исходы по таргетам за последние 30 минут: есть ли вообще 200 (≈5 с)
sqlite3 -readonly -header -column $DB "select combo_name, provider, model, status, count(*) n,
  round(avg(duration)/1000.0) avg_s, max(duration)/1000 max_s from call_logs
  where timestamp > strftime('%Y-%m-%dT%H:%M', 'now', '-30 minutes') and model!='connection-test'
  group by 1,2,3,4 order by 1,2,4"

# 3. Доходит ли комбо дальше первого таргета (≈5 с)
tail -n 30000 $LOG | grep -oE 'Trying model [0-9]+/[0-9]+: [^"]+' | sed 's/ (retry.*//' | sort | uniq -c
tail -n 30000 $LOG | grep -oE 'did not start within [0-9]+ms|Fetch timeout after [0-9]+ms on https://[^/]+|Combo loop safety timeout|Client disconnected \(499\) during [^ ]+' \
  | sort | uniq -c | sort -rn | head

# 4. Живы ли подключения первых таргетов (≈2 с)
sqlite3 -readonly $DB "select provider, is_active, test_status, substr(last_error,1,80)
  from provider_connections where provider in ('claude','codex','antigravity','gemini','kilocode','grok-cli')"
omniroute resilience lockouts      # quota_exhausted / lockout по аккаунтам

# 5. Таймауты, которые сейчас в силе (≈2 с)
omniroute resilience status        # comboCooldownWait.budgetMs, requestQueue.*
sqlite3 -readonly $DB "select name, json_extract(data,'$.config') from combos where name in ('free-stack','hard')"

# 6. Сеть или провайдер (≈30 с)
nmcli -t -f STATE general
journalctl --since -2h -u NetworkManager -o short-iso | grep -E 'CONNECTED_(SITE|GLOBAL|LOCAL)'
for i in $(seq 6); do for u in https://api.kilo.ai/ https://generativelanguage.googleapis.com/ https://api.anthropic.com/; do
  curl -s -o /dev/null -m 5 -w "%{http_code}/%{time_total}s " $u; done; echo; done
sqlite3 -readonly $DB "select substr(timestamp,1,13) h, sum(status=200) ok, sum(status!=200) bad
  from call_logs where model='connection-test' and timestamp > strftime('%Y-%m-%dT%H', 'now', '-6 hours') group by 1"
```

Как читать:

- В (2) нет ни одного 200, а в (6) `connection-test` к многим провайдерам «bad» и curl
  даёт `000/5.0s`: проблема в сети. Прогон надо отложить, менять комбо бессмысленно.
- В (3) почти всё `Trying model 1/N` и `Client disconnected during <таргет 1>`: таймаут
  вызывающего меньше бюджета первого таргета (см. рекомендации 2–5).
- В (4) у `claude` `expired`: нужна переавторизация в дашборде, ждать бесполезно.
- В (2) у `antigravity` 504 через ~66 с, повторяющиеся ×4: это ротация аккаунтов, и
  следующий таргет `agy/*` пройдёт по тому же кругу.
