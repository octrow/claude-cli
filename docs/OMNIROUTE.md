# OmniRoute — как через него идёт вызов и что делать, когда он падает

Вызов модели с инструментами (и любой откат) — это `omniroute run claude -- -p …`, собранный в
`runner.build_args`; вызовы без инструментов с 2026-10 идут по HTTP шлюза, см.
[HTTP route](#http-route-2026-10). Прямых HTTP-запросов к Anthropic нет, `ANTHROPIC_API_KEY` не используется:
авторизация — подписочная, через Claude Code. Отсюда правило, которое стоит выучить раньше
остальных: **сбой шлюза выглядит как сбой модели, и лечится совсем иначе.**

Проверено на локальной установке `omniroute 3.8.50` (`omniroute -v` на этой
машине отвечает `3.8.50` от 2026-09-07); документация владельца —
[release/v3.8.51](https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.51/docs/ops/PROXY_GUIDE.md).
Номера версий здесь не переписывать по памяти: `omniroute -v` скажет правду.

## Порядок диагностики

Сначала дешёвое. Ни одна команда из первых четырёх не тратит квоту:

| Шаг | Команда | Что покажет |
|---|---|---|
| 1 | `omniroute health` | Поднят ли сервер и его компоненты |
| 2 | `omniroute doctor` | Целостность установки, нативные зависимости |
| 3 | `omniroute run claude --dry-run --json` | Какая команда и какие env-ключи ушли бы — **без запуска** |
| 4 | `omniroute logs` | Журнал запросов: что шлюз реально отправил и получил |
| 5 | `omniroute quota` | Квоты провайдеров |
| 6 | `omniroute simulate <prompt>` | Какие провайдеры были бы выбраны, без обращения к upstream |

`omniroute status`, `omniroute models`, `omniroute combo` и `omniroute test <provider> <model>`
отвечают на вопрос «а этот маршрут вообще существует».

## 503 — два разных зверя под одним номером

Шлюз отвечает `503` и когда квота исчерпана, и когда **ни один таргет не подошёл**
(`ALL_TARGETS_SKIPPED`). Второе — ошибка конфигурации, а не нехватка лимита:
ограниченный API-ключ (`allowedModels` содержит только имена комбо), комбо, которое ни во что не
разрешается, модель, которой нет в каталоге. Ждать бесполезно —
[issue #12886](https://github.com/diegosouzapw/OmniRoute/issues/12886) ровно про этот случай.

Поэтому `ALL_TARGETS_SKIPPED` намеренно **не** попал в `runner._USAGE_LIMIT_RE`: расширить
регулярку значит превратить чинибельную опечатку в конфиге в `UsageLimitError`, который у
вызывающего кода означает «останови прогон, квота кончилась». Вместо этого такой сбой остаётся
обычным `ClaudeCliError` и получает в текст ссылку на доки и issues (`runner._gateway_hint`).

## Прочие узнаваемые строки

- `socket hang up` — прокси рвёт простаивающие соединения; в PROXY_GUIDE лечится отключением
  keep-alive (`keepAliveTimeout: 1`, `pipelining: 0`).
- `unsupported_country_region_territory` — географическое ограничение провайдера, не ваш код.
- `ECONNREFUSED` — сервер OmniRoute не запущен: `omniroute serve` (порт по умолчанию `20128`).
- `claude-code:unrecognized_model` — в `--model` уехал не-Claude id. С `omniroute run` (он
  выставляет `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY`) это **только предупреждение**:
  проверено 2026-09-24 на Claude Code 2.1.281 — `--model free-stack` (имя комбо) и
  `--model cohere/command-a-03-2025` (один `provider/model`) дошли до шлюза и ответили.
  Префикс `combo/<имя>` не нужен: комбо зовут просто по имени. `runner.MODELS` — по-прежнему
  только алиасы, это список для выбора по умолчанию, а не граница допустимого.
- `--provider <id>` у `omniroute run` без `--model` **не меняет маршрут** (dry-run: `model: null`).
  Закрепить конкретную цель — это передать её целым id как модель Claude:
  `--model codex/gpt-5.6-terra`. Так в `jobs/pipeline` пинит combo fallback.
- Ошибка комбо называет только **последнюю** цель, которую роутер попробовал («All credentials for
  model gemini-3.8-flash are cooling down»), а не первую. Настоящие причины по каждой цели — в
  `omniroute logs` или прямым запросом `curl /v1/chat/completions` с `"model": "<provider/model>"`.
  `simulate` показывает `quotaAvailable` по своим счётчикам и не видит 429 подписки и порогов
  квоты, заданных в самом OmniRoute.
- Claude Code сам повторяет 429/5xx до ~10 раз с backoff — это ~4 минуты на отказ мёртвого комбо.
  `CLAUDE_CODE_MAX_RETRIES=<n>` в окружении это ограничивает (с `1` отказ за ~10 с). Библиотека
  по умолчанию ставит `2` (`runner.DEFAULT_MAX_RETRIES`); значение из окружения вызывающего
  главнее.
- Баннеры `Loaded env from …` и предупреждения про игнорируемые переменные `.env` печатаются в
  **stdout и stderr до** настоящего ответа. Их снимает `parsing.strip_omniroute_noise`; если вы
  видите их в тексте ошибки — значит настоящая причина уехала за обрезку, а не отсутствует.

- Висит в `status: requesting` без ошибки, `omniroute health` = healthy — смотреть
  `~/.omniroute/logs/application/app.log` и сеть машины (`curl -4` против обычного `curl`):
  разбор в [INCIDENT-2026-10-02-omniroute-hang.md](INCIDENT-2026-10-02-omniroute-hang.md).

## Где искать ответ

1. [Документация OmniRoute](https://github.com/diegosouzapw/OmniRoute#-documentation) — общая карта.
2. [PROXY_GUIDE](https://github.com/diegosouzapw/OmniRoute/blob/release/v3.8.51/docs/ops/PROXY_GUIDE.md)
   — маршрутизация через прокси, health-check, переменные `ENABLE_SOCKS5_PROXY`,
   `PROXY_FAST_FAIL_TIMEOUT_MS`, `PROXY_HEALTH_CACHE_TTL_MS`, `PROXY_AUTO_DISABLE`.
3. [Issues](https://github.com/diegosouzapw/OmniRoute/issues) — искать **по точной строке ошибки**
   (`ALL_TARGETS_SKIPPED`, `unsupported_country_region_territory`), а не по описанию симптома.
   Репозиторий живой, правки приходят ежедневно; сначала `--state all`, потому что нужный ответ
   часто в уже закрытом issue.

## Чего не делать

- **Не передавать `--bare`.** Он включает режим только по `ANTHROPIC_API_KEY` и ломает
  подписочную авторизацию. Флага намеренно нет в `build_args`.
- **Не расширять `_USAGE_LIMIT_RE` «на всякий случай».** Она узкая осознанно: голое `limit`
  ловит `context limit exceeded` — локальную ошибку одного слишком длинного промпта — и роняет
  из-за неё весь прогон.
- **Не чинить недоверенный воркспейс правкой прав.** `Ignoring N permissions.allow entries: this
  workspace has not been trusted` — это про `hasTrustDialogAccepted` в `~/.claude.json` для той
  `cwd`, с которой запущен вызов (у нас — проектный режим, `cwd=tools/cv-adapter`).

## Как выбирается комбо

Комбо выбирает только `--model`, больше ничего. Есть два способа:

- **Через маппинг.** `haiku`/`sonnet`/`opus` раскрываются в `claude-*-…`, и шлюз находит
  комбо по `model_combo_mappings` (`/api/model-combo-mappings`). Побеждает правило с большим
  priority. Состояние на 2026-10-02:

  | pattern | priority | combo |
  |---|---|---|
  | `claude-haiku-*` | 100 | `free-first` (подписка 10-й, последней ступенью) |
  | `claude-sonnet-*` | 100 | `free-stack` (подписки нет совсем) |
  | `claude-opus-*` | 100 | `hard` (подписка первой) |
  | `claude-*` | 10 | `sub-first` |

- **Явно, по имени комбо.** Голое имя комбо в `--model` (`model="sub-first"`) доходит до
  шлюза (`POST /v1/messages | sub-first`), это проверено 2026-10-02. Список комбо выдаёт
  `omniroute combo list`: `sub-first`, `free-first`, `sub-first-opus`, `sonnet-wide`,
  `free-stack`, `hard`.

`tier=`/`profile=` (`run_claude`, `stream_claude`, `arun_claude`) комбо **не выбирают**. Они
превращаются в gateway-флаг `--profile <имя>` до `--`. У цели `claude` в OmniRoute 3.8.50 этот
флаг задаёт только `CLAUDE_CONFIG_DIR=~/.claude/profiles/<имя>` (`bin/cli/commands/run.mjs`,
`launch.mjs`), то есть отдельную папку настроек Claude Code. Пустая папка не загружает
`~/.claude` (CLAUDE.md, rules, хуки, плагины), и только поэтому такой вызов легче. Маршрут
остаётся тем же. Комбо `ultra`/`high`/`middle`/`low` в шлюзе нет, поэтому `tier="high"` создаёт
пустой `~/.claude/profiles/high`, а запрос идёт по маппингам выше. Значение
`ClaudeResult.tier` — это просто переданная строка.

`dry_run_plan()` показывает план (`run claude --dry-run --json`), ничего не запуская: только
команду и ИМЕНА env-ключей. Значений секретов там нет и быть не должно. Разбор, где это
выяснилось: [`OMNIROUTE-HANG-2026-10-02.md`](OMNIROUTE-HANG-2026-10-02.md), «Дополнение 2».

## Preflight из кода (без квоты)

```python
from claude_cli import health, doctor, quota_status, simulate, dry_run_plan

ok, detail = health()          # omniroute health
ok, detail = doctor()          # omniroute doctor
ok, detail = quota_status()    # omniroute quota
ok, detail = simulate("hi", combo="free-first")  # ни одного upstream-вызова
ok, plan = dry_run_plan()      # plan — JSON {"command", "args", "env_keys"}, без запуска
```

Все хелперы, включая `dry_run_plan`, возвращают `(bool, str)` и никогда не бросают:
нет бинарника, таймаут, `OSError`, сломанный план — это `(False, <чистая причина>)`. Матрица
use-vs-avoid всего покрытия v3.8.51 живёт в `claude_cli.preflight.MATRIX`
(норматив), этот файл — человеческая версия.

## HTTP route (2026-10)

С 2026-10 `run_claude`/`arun_claude` для вызовов без инструментов идут в HTTP API шлюза, а не
через `omniroute run claude`; CLI остаётся откатом. Код: `claude_cli/http_api.py`,
`runner._http_first`.

**Маршрутизация.** `POST {OMNIROUTE_URL или http://localhost:20128}/v1/chat/completions`
(формат OpenAI, `Authorization: Bearer` из `OMNIROUTE_API_KEY` в env или из строки
`OMNIROUTE_API_KEY` в `~/.omniroute/.env`). Нет ключа — HTTP молча пропускается. На CLI
остаются вызовы с: `tools`/`allowed_tools`, `cwd` проекта (в нём есть `CLAUDE.md` или
`.claude/`; нейтральный пустой cwd, как у jobs `draft_stages`, HTTP не мешает),
`slash_commands`, переопределениями remote/base_url/context/provider,
`output_format="stream-json"` (кто читает сырой поток событий CLI). `stream_claude` тоже
идёт по HTTP, если вызов без инструментов; тогда `on_event` не получает ни одного события
(ответ приходит целиком, живость показывайте своим heartbeat). `ping` (`backend="cli"`) —
только CLI.

**Откат.** Сбой HTTP (`HttpLlmError`, подкласс `ClaudeCliError`) -> warning -> тот же вызов
через CLI; HTTP после этого выключен на 600 с на процесс (все модели, любая ошибка, включая
4xx). `backend="api"` — только HTTP: ошибки летят наружу, cooldown не включается.

**Отключение.** kwarg `backend="auto"|"cli"|"api"`; env `CLAUDE_CLI_BACKEND` перекрывает kwarg.
Тесты ставят `cli` autouse-фикстурой в `tests/conftest.py`: живых вызовов шлюза в тестах нет.

**Модели.** `haiku`/`sonnet` -> `free-stack`, `opus` -> `hard` (`COMBOS` в `http_api.py`);
прочие значения (голое имя комбо, например `sub-first`) проходят как есть. Глоб-маппинг
model -> combo на стороне шлюза действует только для CLI; по HTTP имя комбо шлётся сразу.

**Результат.** `ClaudeResult(text, cost_usd=0.0, duration_ms, raw)`; `raw` — тело ответа
OpenAI, где `usage` переписан в ключи CLI-конверта `input_tokens`/`output_tokens`, чтобы
счётчики вроде `normalize_usage` в jobs работали без правок. `stdout` пуст, тир не
возвращается, `effort` и прочие CLI-only параметры по HTTP игнорируются.

**Зачем.** Каждый CLI-вызов поднимал Claude Code (процесс Node + его системный промпт), чтобы
переслать один промпт в тот же шлюз. Замер 2026-10-13: «pong» по HTTP ~3 с, 83 входных
токена. Бесплатные ступени ограничены по TPM (поэтому у tlf `digest.batch_chars 12000`), и
оверхед системного промпта CLI тратил этот бюджет. OAuth-подключение подписки Claude в
OmniRoute протухло 2026-09-23, так что CLI-путь качество подписки уже не давал.

### Decisions

* **Кэш остаётся в pylibs** (`cached_run`/`FileCacheStore`: SQLite, ключ = модель + routing
  kwargs + промпт + purpose + version + tools; промпт по умолчанию не хранится). Он не зависит
  от раннера: оборачивает `run_claude`, HTTP-маршрут внутри; ключи старых вызовов не
  изменились. Маршрут (http/cli) в ключ **не входит**: HTTP-ответ обслужит позднейший
  CLI-вызов с тем же промптом (так задумано).
* **Семантический кэш OmniRoute отвергнут** как замена: работает только при явном
  `temperature=0`, TTL 30–60 мин, LRU 50–100 записей (память + sqlite), ключ игнорирует
  system/tools (риск коллизий), ключ привязан к целевому провайдеру (фолбэк комбо = промах),
  ответы лежат в его `storage.sqlite`.
* **`temperature` не шлётся никогда** — кэш OmniRoute остаётся выключенным, двойного кэша нет.
  `PROMPT_CACHE_*` в OmniRoute — мёртвый код; passthrough `cache_control` Anthropic работает
  только для клиентов Claude Code на кэширующих провайдерах, на бесплатных ступенях
  неактуален. Если понадобится отказ по запросу: заголовок `X-OmniRoute-No-Cache: true`.
* **`stream-json` остаётся на CLI**: вызывающие читают сырой поток. Потребителям, которым
  нужен только текст, переходить на `json` (по умолчанию).
* **Ошибка CLI в json-режиме** берёт текст из `result` конверта (раньше только для
  `stream-json`): теперь `json` — основной режим и для отката.
* **Cooldown 600 с — на процесс, не на модель**: мёртвый шлюз стоит один таймаут за окно.
* **`cost_usd=0.0`** для HTTP-ответов (цены у шлюза нет); учёт ведите по `usage`.

### Migration notes for consumers

Менять код не нужно, что меняется и как отключить (`backend="cli"` или
`CLAUDE_CLI_BACKEND=cli`):

* **Переехали автоматически:** jobs (ranking/scoring через `run_claude`), dialogue-lens, finance-insights,
  buy-smarter (`arun_claude`; его retry на `sub-first` тоже сначала идёт по HTTP; стоимость
  HTTP-ответов теперь 0.0), get_cool_event extract (переведён `stream-json` -> `json`,
  5d6aa78), tlf topics (`stream-json` -> `json`, 85d4ffe), tlf digest (`stream-json` ->
  `json`; в `digests/runs/*/raw.jsonl` теперь конверт ответа: CLI JSON или тело OpenAI,
  если ответил HTTP; `meta.json` без `result`/`choices`).
* **jobs `draft_stages`, `apply_research`, `cv_reuse` verify** (`stream_claude` без
  инструментов) — по HTTP с 2026-10-13. Счётчик токенов по событиям потока
  (`llm.StreamUsage`) для них пуст, учёт идёт по `raw.usage` (`normalize_usage`).
* **Тесты потребителей:** `run_claude` находит ключ в `~/.omniroute/.env`, поэтому тесты,
  которые подменяют CLI на уровне `subprocess.Popen`, без защиты ушли бы в живой шлюз. Такие
  защиты выставляют `CLAUDE_CLI_BACKEND=cli`: conftest в jobs, tlf и finance-insights,
  `fx.guard_live_cli` в chat-watch. Новому потребителю нужно то же самое.

#### Что осталось на CLI (2026-10-13)

| Где | Вызов | Почему CLI | Что нужно для переезда |
|---|---|---|---|
| jobs `apply_gate.py` | `stream_claude`, tools `WebSearch` | веб-поиск — инструмент Claude Code | свой поиск (SearXNG/Kindly) + HTTP |
| chat-watch `vision.py` фолбэк | `run_claude`, tool `Read` | читает файл картинки | основной путь уже прямой HTTP с картинкой |
| tlf/прочие `ping`, `health` | `ping` (`backend="cli"`) | проверяет сам CLI | не переводить |

Прочие потребители (`run_claude`/`arun_claude`/`stream_claude` без tools) идут по HTTP.
* **chat-watch:** собственный маршрут `api` и `_omniroute_chat` удалены (a9410de); маршруты
  теперь default -> fallback (`sub-first`), HTTP делает сам `run_claude`. Прямой HTTP остался
  только для vision (картинки) и Jev (`/v1/systemone`). repo-scout и раньше ходил напрямую.
