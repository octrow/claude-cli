# OmniRoute — как через него идёт вызов и что делать, когда он падает

Каждый вызов модели в этих проектах — это `omniroute run claude -- -p …`, собранный в
`runner.build_args`. Прямых HTTP-запросов к Anthropic нет, `ANTHROPIC_API_KEY` не используется:
авторизация — подписочная, через Claude Code. Отсюда правило, которое стоит выучить раньше
остальных: **сбой шлюза выглядит как сбой модели, и лечится совсем иначе.**

Проверено на локальной установке `omniroute 3.8.50`; документация владельца —
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
