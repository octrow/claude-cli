# Карта логики

Как один промпт превращается в `ClaudeResult` (или в типизированную ошибку).
Карта описывает код как он есть сегодня, шаг за шагом.
Каждый шаг говорит, что он решает, где поток ветвится и какой модуль это делает.

Зеркало `docs/logic-map.md`. Источник истины — английская версия; правятся в одном коммите.
Термины: «шлюз» — локальный OmniRoute; «комбо» — маршрут OmniRoute (`free-stack`, `hard`, `sub-first`);
«direct» — голый `claude -p` на локальном логине. Подробности шлюза — `docs/OMNIROUTE.md`.
Известные дыры и правки — `docs/plans/2026-11-01-logic-map-fixes.md`.
`tests/test_logic_map.py` проверяет, что каждая строка `Module:` указывает на настоящий модуль.

## Поток одним взглядом

```
промпт + kwargs
  │
  ├─ 1  Direct или шлюз ........ обойти OmniRoute целиком?
  ├─ 2  Проверка CLI-only ...... нужно ли вызову то, что HTTP не несёт?
  ├─ 3  Выбор backend .......... env/kwarg, ключ, cooldown → пробовать HTTP или нет
  ├─ 4  HTTP-вызов ............. POST /v1/chat/completions, одно имя комбо
  ├─ 5  Сбой HTTP .............. откат в CLI (auto) или исключение (api)
  ├─ 6  Argv ................... проверка kwargs, сборка `omniroute run … claude -- -p …`
  ├─ 7  Песочница .............. временный cwd, пустой MCP-конфиг, env дочернего процесса
  ├─ 8  Запуск и ожидание ...... один подпроцесс, таймаут убивает группу процессов
  ├─ 9  Разбор вывода .......... json-конверт или свёрнутое result-событие stream-json
  ├─ 10 Классификация сбоя ..... не залогинен → шлюз исчерпан → лимит → код выхода
  └─ 11 Проверки успеха ........ is_error, пустой result
  │
ClaudeResult | NotLoggedInError | UsageLimitError | ClaudeCliError
```

Ответ по HTTP выходит из потока на шаге 4. Шаги 6–11 идут только на пути CLI.
Ретраев здесь нет: один запуск CLI на вызов (Claude Code ретраит сам внутри, лимит ставит шаг 7).

## Где поток делится по маршруту

До модели ведут три маршрута: HTTP в шлюз, CLI через `omniroute run`, CLI direct.
Маршрут решается в четырёх местах независимыми сигналами, единого реестра нет.

| Маршрут / вариант | Включается | Какие шаги меняет | Что меняется |
|---|---|---|---|
| CLI direct | `direct=True` (есть только у `run_claude`) | 1, 6, 7, 10 | нет префикса `omniroute`, нет HTTP, env шлюза убран, лимит `session_wide=True` |
| CLI принудительно | `tools`, `allowed_tools`, `slash_commands`, remote-опции, `output_format="stream-json"`, `cwd` с `CLAUDE.md` или `.claude` | 2 | HTTP пропущен |
| HTTP выключен | `CLAUDE_CLI_BACKEND`/`backend` = `cli`; нет `OMNIROUTE_API_KEY`; идёт cooldown | 3 | CLI через шлюз |
| Только HTTP | `CLAUDE_CLI_BACKEND`/`backend` = `api` | 3, 5 | без отката и cooldown, летит `HttpLlmError`; всё равно CLI при `direct` или сигнале шага 2 (#14) |
| Модель → комбо | словарь `COMBOS` на HTTP; glob-маппинг шлюза на CLI | 4, 6 | `haiku`/`sonnet` → `free-stack`, `opus` → `hard`; прочее по HTTP уходит как есть |
| Класс сбоя | регэкспы по очищенным stdout+stderr упавшего прогона | 10 | тип исключения, `session_wide`, подсказка про шлюз |

Примеры:
- `run_claude("summarize …")` с ключом в `~/.omniroute/.env` → HTTP, комбо `free-stack`, `cost_usd=0.0`.
- `run_claude("…", tools="Read")` → CLI через шлюз, `--tools Read --allowed-tools Read`.
- `run_claude("…", model="claude-sonnet-5-5", direct=True)` → без `omniroute run`, голый `claude -p`, платит локальный логин.
- `stream_claude("…", on_event)` без инструментов и с ключом → HTTP; `on_event` не вызывается ни разу.

Module: `runner`, `http_api`

## Точки входа

Все вызовы модели идут через шаги 1–11. Различаются умолчаниями и тем, что пробрасывают.

| Вход | Backend | `direct` | Формат вывода | Заметки |
|---|---|---|---|---|
| `run_claude` | `auto` | есть | `json` (kwarg) | блокирующий; единственный вход с `direct` |
| `arun_claude` | `auto` | **нет** | `json` (kwarg) | HTTP-шаг в `asyncio.to_thread`; CLI через `asyncio.create_subprocess_exec` |
| `stream_claude` | `auto` | **нет** | всегда `stream-json` | шагу 2 передаётся `json`, поэтому поток без инструментов идёт по HTTP и событий не шлёт |
| `ping` | kwarg `cli` | нет | `json` | `model="haiku"`, `timeout=90`, промпт "reply with the single word pong"; не бросает; env `CLAUDE_CLI_BACKEND` всё равно перекрывает его `cli` (#1) |
| `ClaudeCliProvider.ask` | `auto` | через `**kwargs` | `json` | `DEFAULT_MODEL="sonnet"`, `DEFAULT_TIMEOUT=300`; возвращает только `.text` |
| `cached_run(runner, store, …)` | как у runner | как у runner | как у runner | свой `model="sonnet"` по умолчанию, передаётся всегда; см. боковые потоки |

Общие умолчания: `model="sonnet"`, `effort="medium"`, `timeout=300`, `tools=""`, `setting_sources="user"`, все опции шлюза `None`.
Консольной команды нет: пакет — только библиотека.

Module: `runner`, `cache`

## Шаги

### 1. Direct или шлюз

Решает: используется ли OmniRoute вообще.
Вход: kwarg `direct`.
Ветки:
- `direct=True` (только `run_claude`) → шаги 2–5 пропущены, шаг 6 без префикса шлюза;
- иначе → шаг 2.
Сбой: нет.
Module: `runner`

### 2. Проверка CLI-only

Решает: нужно ли вызову то, что несёт только CLI.
Вход: `tools`, `allowed_tools`, `slash_commands`, `cwd`, remote-опции, `output_format`. Выход: bool `cli_only`.
Ветки:
- задан любой из `tools`, `allowed_tools`, `slash_commands`, `remote`/`base_url`/`context`/`provider` → CLI;
- `output_format == "stream-json"` → CLI (`stream_claude` передаёт сюда `"json"`, так что не считается);
- `cwd` с `CLAUDE.md` или `.claude` → CLI; пустой или нейтральный `cwd` не считается.
Не учитываются: `effort`, `tier`, `profile`, `setting_sources`, `permission_mode` (HTTP молча их отбрасывает).
Сбой: нет (только `exists` по файловой системе).
Module: `runner`

### 3. Выбор backend

Решает: пробовать HTTP сейчас или идти в CLI.
Вход: kwarg `backend`, env `CLAUDE_CLI_BACKEND`, `cli_only`, cooldown, ключ.
Ветки:
- непустой env `CLAUDE_CLI_BACKEND` заменяет kwarg (и для `ping` тоже);
- `cli` или `cli_only` → CLI (проверяется раньше `api`, поэтому `api` + tools всё равно запускает CLI);
- `api` → шаг 4, ошибки летят наружу;
- любое другое значение (`auto`, `CLI`, `http` …) работает как `auto`: шаг 4, только если cooldown процесса прошёл и `api_key()` нашёл ключ.
Без ключа: env `OMNIROUTE_API_KEY`, иначе первая строка `OMNIROUTE_API_KEY=…` (можно с `export `, кавычки снимаются) в `~/.omniroute/.env`; пустое значение или нечитаемый файл → ключа нет → CLI.
Сбой: нет.
Module: `http_api`

### 4. HTTP-вызов

Решает: ответ через OpenAI-совместимый API шлюза.
Вход: промпт, `model`, `timeout`. Выход: `ClaudeResult(text, cost_usd=0.0, duration_ms, raw)`.
Ветки:
- URL `OMNIROUTE_URL` (по умолчанию `http://localhost:20128`) + `/v1/chat/completions`: https куда угодно, http только на loopback, `user:pass@` отклоняется;
- `model` есть в `COMBOS` → `free-stack`/`hard`; любое другое значение уходит как есть;
- тело только `{"model", "messages"}`: без `temperature` (он включил бы семантический кэш шлюза), без `effort`.
Редиректы не выполняются (ключ ушёл бы повторно). Тело ограничено 8 MiB; дедлайн проверяется только между чтениями тела, так что connect, заголовки и одно зависшее чтение получают каждый свой `timeout`.
`raw` хранит тело OpenAI, `usage` переименован в `input_tokens`/`output_tokens`. `stdout=""`, `tier=None`.
Сбой: транспорт, статус, JSON, форма `choices` или пустой текст → `HttpLlmError` (подкласс `ClaudeCliError`). Не-dict `usage` даёт `AttributeError`, и шаг 5 не срабатывает (#15).
Module: `http_api`

### 5. Сбой HTTP

Решает: заканчивает ли ошибка HTTP вызов.
Ветки:
- `auto` + `HttpLlmError` → warning в лог, `_down_until = now + 600 с` на весь процесс (все модели, любой статус, включая 4xx), дальше шаг 6;
- любое другое исключение → доходит до вызывающего, без отката и cooldown;
- `api` → `HttpLlmError` доходит до вызывающего только с `HTTP <code> from <url>`; `UsageLimitError` не бывает (#5).
Module: `http_api`

### 6. Argv

Решает: точную командную строку `claude`.
Вход: все CLI-kwargs. Выход: `list[str]`.
Ветки:
- `tier` не из `ultra/high/middle/low` → `ValueError`; `direct` с любой опцией шлюза → `ValueError`;
- любой строковый kwarg, начинающийся с `-` → `ValueError` (иначе разобрался бы как флаг);
- шлюз: `omniroute run [--remote …] [--base-url …] [--context …] [--provider …] [--profile <profile или tier>] claude -- -p`;
- direct: `claude -p`;
- дальше `--model`, `--effort` (нет при `None`), `--output-format`; stream-json добавляет `--include-partial-messages --verbose`;
- всегда `--no-session-persistence --tools <tools>`; непустой `tools` добавляет `--allowed-tools <allowed_tools или tools>`; `allowed_tools` без `tools` теряется (#16);
- `--disable-slash-commands`, если не `slash_commands`; `--permission-mode`, если задан;
- всегда `--setting-sources <setting_sources> --strict-mcp-config --mcp-config <пустой файл>`.
`--bare` не добавляется никогда. `tier`/`profile` выбирают только `CLAUDE_CONFIG_DIR` внутри шлюза, не комбо.
Сбой: `ValueError` до запуска процесса. Проверка стоит здесь, после шагов 3–5: по HTTP плохой `tier` или `model` с `-` проходит (#11).
Module: `runner`, `preflight`

### 7. Песочница

Решает: что видит дочерний процесс.
Ветки:
- `TemporaryDirectory(prefix="claude-cli-")` создаётся всегда и хранит `mcp-empty.json` = `{"mcpServers":{}}`, удаляется после вызова;
- задан `cwd` → процесс запускается там; иначе во временном каталоге;
- env процесса = `CLAUDE_CODE_MAX_RETRIES=2`, поверх `os.environ` (значение вызывающего побеждает);
- direct → удаляются `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`.
Сбой: нет.
Module: `runner`

### 8. Запуск и ожидание

Решает: когда прогон закончен.
Вход: argv, промпт в stdin. Выход: returncode, stdout, stderr.
Ветки:
- `run_claude`: `Popen(start_new_session=True)` + `communicate(timeout)`;
- `arun_claude`: `create_subprocess_exec` + `wait_for(timeout)`;
- `stream_claude`: stdout читается построчно, каждая JSON-строка-dict → `on_event`; stderr сливается в потоке; `threading.Timer` убивает по `timeout`, даже если процесс молчит.
Сбой: нет бинарника `omniroute`/`claude` или нет каталога `cwd` → голый `FileNotFoundError`, не `ClaudeCliError` (#12). Таймаут → SIGKILL группе процессов (настоящий `claude` под `omniroute run` тоже умирает) → `ClaudeCliError("claude timed out after Ns")`, stdout не сохраняется. Ctrl-C, отмена или исключение в `on_event` → kill, затем исключение дальше.
Module: `runner`

### 9. Разбор вывода

Решает: dict-конверт, который читают следующие шаги.
Ветки:
- `json` → `json.loads(stdout)`; при ошибке `raw_decode` с первой `{` (баннер шлюза до, шум после);
- `stream-json` → все строки `{"type":"result"}` сливаются по ключам: `result` плюс `usage`, `total_cost_usd`, `duration_ms`, `duration_api_ms`, `num_turns`, `session_id`, `model`, `modelUsage`, `is_error`.
Сбой: не разобралось → здесь `{}`; при нулевом коде шаг 11 бросает `claude returned non-JSON: …` (json), `unexpected claude envelope: <type>` (json, не dict) или `claude produced no result` (stream-json).
Module: `parsing`

### 10. Классификация сбоя

Решает: какую ошибку бросает упавший прогон. Работает только при коде ≠ 0 или `is_error`.
Вход: stdout+stderr без ANSI-кодов и трёх видов баннеров OmniRoute.
Ветки, побеждает первое совпадение:
- "Not logged in" где угодно → `NotLoggedInError`;
- шлюз исчерпан (`ALL_TARGETS_SKIPPED`, `no provider matched`, `fallback exhausted`, `allowedModels`, `PROXY_FAST_FAIL`, `circuit breaker` …) → `ClaudeCliError("omniroute gateway error: …")` + подсказка;
- лимит (`usage limit`, `session limit`, `rate limit`, `quota exceeded`, `too many requests`, `429`) → `UsageLimitError`, `session_wide = not via_gateway`; сообщение — совпавшая строка;
- код ≠ 0, ничего не совпало → `ClaudeCliError("claude exited N: <detail>")`.
Шлюз раньше лимита намеренно: "combo 'hard': fallback exhausted (429)" совпадает с обоими.
Detail: хвост stderr 500 символов, иначе текст result, иначе последняя не-JSON строка stdout.
Подсказка: любой широкий маркер шлюза (`502`, `503`, `omniroute`, `ECONNREFUSED`, `unrecognized_model` …) добавляет дешёвые команды диагностики `omniroute` и ссылки на доки. Direct-прогоны тоже её получают (#7).
`arun_claude` и `stream_claude` оставляют `via_gateway` по умолчанию `True`.
Сбой: каждая ошибка несёт `.stdout`.
Module: `runner`, `parsing`, `errors`

### 11. Проверки успеха

Решает: ответил ли прогон с нулевым кодом на самом деле.
Ветки:
- `is_error` истинно → `ClaudeCliError("claude error: <последние 300 символов>")`;
- `result` нет, пустой или не строка → `ClaudeCliError("claude produced no result")`;
- иначе `ClaudeResult`; `duration_ms` из конверта, иначе по часам.
Module: `runner`

### Выход

`ClaudeResult`: `text`, `cost_usd` (`total_cost_usd` конверта; `0.0` по HTTP), `duration_ms`, `raw` (конверт или тело OpenAI), `stdout` (сырой вывод CLI; `""` по HTTP), `tier` (эхо kwarg; `None` по HTTP).
Ошибки: `NotLoggedInError`, `UsageLimitError(session_wide)`, `HttpLlmError`, `ClaudeCliError`; все — `RuntimeError`. Вне семейства: `ValueError` на плохие kwargs, `FileNotFoundError` при запуске (#12).
Какой маршрут ответил, не записывается; HTTP отличает только `stdout == ""`.
Module: `runner`, `errors`

## Боковые потоки

### Кэш ответов (`cached_run`)

Ключ: sha256 по модели, промпту со схлопнутыми пробелами и `schema`/`version`/`purpose`/`tools`, если заданы.
Routing-kwargs `tier`, `provider`, `profile`, `remote`, `base_url`, `context` входят в часть модели, если заданы; `direct`, `backend`, `effort`, `allowed_tools`, `cwd` — нет (#3).
Попадание → сохранённый текст, runner не вызывается. `refresh=True` пропускает чтение. Возвращается только `.text`.
Запись: одна строка на промах; колонка `prompt` NULL, если не `store_prompt=True`. Ошибка записи (`sqlite3.Error`, `OSError`) в лог, не наружу; ошибки чтения и `FileCacheStore()` летят наружу.
`FileCacheStore`: sqlite-таблица `claude_cli_cache`; новый каталог 0700, новый файл 0600 (umask действует; существующие сохраняют права); `max_age_days` делает старые строки промахами, ничего не удаляется.
Module: `cache`

### Пакеты (`pack_batches`, `run_chunks`)

`pack_batches`: жадно, порядок сохраняется; пакет закрывается перед элементом, который превысил бы `budget` или `max_items` (`<= 0` выключает); слишком большой элемент едет один.
`run_chunks`: `fn(chunk) -> (data, cost)`; `workers > 1` → пул потоков, результаты в порядке завершения. Любой `Exception` → `"<label>: <Type>: <120 символов>"` в `failed`, пакет продолжается, в том числе после `NotLoggedInError` и лимитов на всю сессию (#6).
Module: `batch`

### Проверка здоровья (`ping`)

`run_claude` с `backend="cli"`; возвращает `(True, "pong via omniroute, Ns")` или `(False, <очищенная ошибка ≤200 символов>)`.
Module: `runner`

### Preflight (`health`, `doctor`, `quota_status`, `simulate`, `dry_run_plan`)

Каждый запускает одну бесплатную по квоте `omniroute <subcommand>` с таймаутом и возвращает `(ok, detail)`, не бросает; detail — хвост очищенного вывода, 500 символов (`"ok"` / `"exit N"`, если пусто; компактный JSON у `dry_run_plan`).
`simulate` ставит `--` перед промптом. `dry_run_plan` возвращает `{"command","args","env_keys"}` только с именами env-ключей.
`TIERS`, `MATRIX`, `GUIDES`, `BENCHMARKS`, `CONSTRAINTS`, `QUOTA_FREE_SUBCOMMANDS` — данные; код читает только ключи `TIERS` (шаг 6). `QUOTA_FREE_SUBCOMMANDS` в рантайме ничто не проверяет.
Module: `preflight`

### JSON из текста (`extract_json`, `extract_json_or_none`)

Снимает fence, пробует блок `{…}` в fence, затем `raw_decode` только с первой `{` (лишняя `{` перед объектом ломает разбор, #18; в массиве побеждает первый объект). Нет `{` → `json.loads` всего текста (`"[1,2]"` разбирается). `_or_none` возвращает `None` на `ValueError`/`TypeError`.
Module: `parsing`

## Известные дыры

Подробности и правки — в плане (номер в скобках).
- `CLAUDE_CLI_BACKEND` перекрывает `backend="cli"` у `ping`: `ping` может пройти по HTTP, не запустив `claude` (#1).
- `timeout` действует на маршрут: auto-вызов может потратить таймаут HTTP (жёсткого предела там нет) и потом полный таймаут CLI (#2).
- ключ `cached_run` не учитывает `direct` и `effort`: ответ настоящего Claude и ответ `free-stack` делят одну строку (#3).
- HTTP шлёт id моделей вне `COMBOS` как есть, CLI маппит их по glob; один вызов может попасть в разные комбо (#4, не подтверждено).
- `backend="api"` никогда не бросает `UsageLimitError` и теряет тело ошибки (#5).
- `run_chunks` продолжает после `NotLoggedInError` и лимитов на всю сессию (#6, вопрос).
- проверка kwargs идёт после попытки HTTP: плохой `tier` или `model` падает только в дни CLI (#11).
- нет бинарника или `cwd` → `FileNotFoundError`, вне контракта `RuntimeError` (#12).
- один отклонённый запрос (4xx) выключает HTTP на весь процесс на 600 с (#13, вопрос).
