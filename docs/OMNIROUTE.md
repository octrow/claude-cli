# OmniRoute — как через него идёт вызов и что делать, когда он падает

Каждый вызов модели в этих проектах — это `omniroute run claude -- -p …`, собранный в
`runner.build_args`. Прямых HTTP-запросов к Anthropic нет, `ANTHROPIC_API_KEY` не используется:
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
- `claude-code:unrecognized_model` — в `--model` уехало `combo/<имя>`. Claude Code отбрасывает
  незнакомый id локально, запрос до шлюза не доходит. Отсюда `runner.MODELS` — только
  `haiku`/`sonnet`/`opus`.
- Баннеры `Loaded env from …` и предупреждения про игнорируемые переменные `.env` печатаются в
  **stdout и stderr до** настоящего ответа. Их снимает `parsing.strip_omniroute_noise`; если вы
  видите их в тексте ошибки — значит настоящая причина уехала за обрезку, а не отсутствует.

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

## Четыре тира комбо: сначала free, потом подписка

Цель — выпить досуха ВСЕ free-источники, а платное качество разложить по четырём
тирам. Каждый тир — это комбо на стороне OmniRoute (Dashboard → Routing, или
`omniroute combo create/list`, маппинги — `/api/model-combo-mappings`): внутри
каждого комбо free-таргеты идут первыми, платная подписка — последней
(FREE_TIERS + AUTO-COMBO). Проверено локально 2026-09-07: `omniroute combo list`
показывает `sub-first`, `free-first`, `sub-first-opus`, `sonnet-wide`,
`static-best-coding`; `omniroute simulate --combo free-first "<prompt>"` и
`omniroute run claude --dry-run --json` отрабатывают без траты квоты.

| Тир | Класс | Примеры членства | Бенчмарк-источник |
|---|---|---|---|
| ULTRA | frontier | fable-5.1-class, GPT-6 Astra-class | OpenRouter session-cost, BenchLM arena Elo, LiveBench cost/task, Artificial Analysis |
| HIGH | opus | GPT-5.6 Sol-class, Gemini 3.8 Flash-class | те же четыре |
| MIDDLE | sonnet | GPT-5.6 Terra-class, Muse Spark 1.2-class, Grok 4.6-class, Claude Opus 4.7-class | те же четыре |
| LOW | bulk-cheap | DeepSeek V4 Flash-class, inclusionai-ling-3.0-flash-class | OpenRouter session-cost, LiveBench cost/task |

Состав тиров — данные конфига, а не константы кода: пересмотр — это правка
комбо в OmniRoute + этой таблицы с новой датой ревью, без релиза библиотеки.
Источники состава:
[OpenRouter rankings (session-cost)](https://openrouter.ai/rankings#session-cost),
[BenchLM (arena Elo)](https://benchlm.ai/?status=Current&sort=arenaElo),
[LiveBench (cost/task)](https://livebench.ai/#/?sort=cpst&dir=desc),
[Artificial Analysis](https://artificialanalysis.ai/leaderboards/models).

В коде тир выбирается параметром `tier=` (`run_claude`, `stream_claude`,
`arun_claude`): он превращается в gateway-флаг `--profile <тир>` ДО `--` и
никогда — в `--model combo/<имя>`, потому что Claude Code отбрасывает
незнакомый id локально (`claude-code:unrecognized_model`), и запрос до шлюза не
доходит. `--model` остаётся `haiku`/`sonnet`/`opus`. `ClaudeResult.tier`
возвращает запрошенный тир, а gateway-ошибки называют attempted tier/combo —
видно, какой тир потрачен. `dry_run_plan()` показывает план
(`run claude --dry-run --json`) без запуска: только команда и ИМЕНА env-ключей,
значений секретов там нет и быть не должно.

## Preflight из кода (без квоты)

```python
from claude_cli import health, doctor, quota_status, simulate, dry_run_plan

ok, detail = health()          # omniroute health
ok, detail = doctor()          # omniroute doctor
ok, detail = quota_status()    # omniroute quota
ok, detail = simulate("hi", combo="free-first")  # ни одного upstream-вызова
plan = dry_run_plan()          # {"command", "args", "env_keys"} — без запуска
```

Все хелперы возвращают `(bool, str)` и никогда не бросают (кроме `dry_run_plan`,
который бросает `ClaudeCliError` — тоже `RuntimeError` — если сломан сам план):
нет бинарника, таймаут, `OSError` — это `(False, <чистая причина>)`. Матрица
use-vs-avoid всего покрытия v3.8.51 живёт в `claude_cli.preflight.MATRIX`
(норматив), этот файл — человеческая версия.
