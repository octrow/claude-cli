# План: findings карты логики

Findings, найденные при написании `docs/logic-map.md` 2026-11-01, плюс независимое ревью карты.
Карта описывает код как он есть; этот файл — что в нём не так и что предлагаем. Новые findings дописываются в конец.
Исправленный finding получает ✅, карта обновляется в том же коммите.

Severity: HIGH — неверный результат для пользователя. MEDIUM — результат хуже, чем мог бы быть. LOW — шум, долг, доки.
«Вопрос» — неясно, баг это или намерение; решает владелец. «Не подтверждено» — нужен живой прогон.

## Findings

| # | Шаг карты | Что не так | Предлагаем | Severity |
|---|---|---|---|---|
| 1 ✅ | 3, Health check | `ping` передаёт `backend="cli"` (`runner.py:866`), но `try_http` берёт непустой env `CLAUDE_CLI_BACKEND` поверх kwarg (`http_api.py:161`). При `CLAUDE_CLI_BACKEND=auto` или `api` и наличии ключа `ping` отвечает по HTTP и пишет «pong via omniroute», не запустив `claude`: логин и подписка не проверены. Доки (`docs/OMNIROUTE.md:168`, `:245`) обещают «только CLI». | `ping` идёт мимо `try_http` (вызывать CLI-путь напрямую) или env перекрывает только `auto`; тест с `CLAUDE_CLI_BACKEND=api`. | HIGH |
| 2 ✅ | 3–5, 8 | `timeout` действует на каждый маршрут отдельно. В `auto` HTTP ждёт до `timeout` на connect и до ~2× на зависшее тело (`http_api.py:82`, `:131`), после ошибки CLI получает полный `timeout` заново (`runner.py:566`). `run_claude(timeout=300)` может длиться ~900 с. | Передавать в CLI остаток бюджета (`timeout - elapsed`), минимум несколько секунд; либо задокументировать. | MEDIUM |
| 3 ✅ | Response cache | Ключ `cached_run` не учитывает `direct`, `effort`, `allowed_tools`, `cwd` (`cache.py:163`, `:191`). `cached_run(run_claude, s, p, model="sonnet")` (шлюз → `free-stack`, без Claude) и тот же вызов с `direct=True` (настоящий Sonnet по логину) делят одну строку; второй вызов вернёт ответ первого. | Добавить `direct` и `backend` (при подтверждении #4 HTTP и CLI отвечают из разных комбо) в `_ROUTING_KWARGS`; `effort` складывать в ключ, только если он не `"medium"`, чтобы старые строки не потерялись. | MEDIUM |
| 4 ✅ | 4, split table | По HTTP `COMBOS` (`http_api.py:27`) — ручная копия маппинга шлюза для трёх алиасов; другие id уходят как есть (`:125`). По CLI `model="claude-opus-5-5"` попадает в glob `claude-opus-*` → `hard`, а по HTTP шлётся сырой `claude-opus-5-5`, маппинг для HTTP не применяется (`docs/OMNIROUTE.md:180`). Один и тот же вызов уходит в разные апстримы в зависимости от маршрута; при смене маппинга на шлюзе `COMBOS` молча устаревает. | Не подтверждено: живой запрос `model="claude-opus-5-5"` по HTTP и CLI, сравнить комбо в `omniroute logs`. Если расходятся — непокрытые `COMBOS` id отправлять в CLI (`cli_only`). | MEDIUM |
| 5 ✅ | 5, Output | `backend="api"`: любой HTTP-статус → `HttpLlmError("HTTP <code> from <url>")` (`http_api.py:134-135`), тело ответа отброшено. 429 не становится `UsageLimitError`, `ALL_TARGETS_SKIPPED` неотличим от квоты, подсказки шлюза нет. Docstring `run_claude` обещает `UsageLimitError`/`NotLoggedInError` (`runner.py:501-502`). | Читать тело ошибки (с лимитом) и прогонять через тот же классификатор, что и шаг 10, с `via_gateway=True`. | MEDIUM |
| 6 ✅ | Batches | `run_chunks` ловит любой `Exception` (`batch.py:66-69`), включая `NotLoggedInError` и `UsageLimitError(session_wide=True)`. Пакет на `direct=True` после стены лимита продолжает звать `claude` для каждого куска. `errors.py` говорит «политику abort решает вызывающий», но у `run_chunks` нет способа прервать. | Вопрос: пробрасывать `NotLoggedInError` и `session_wide=True`, или параметр `abort_on=(…)`. Спеки противоречат друг другу: `batch-processing/spec.md:43` — «ни один кусок не прерывает пакет», `run-execution/spec.md:78` — лимит бросается, «чтобы вызывающий прервал пакет». Обойти можно общим флагом внутри `fn`, но при `workers>1` все futures уже отправлены. | MEDIUM |
| 7 ✅ | 10 | Прямой вызов тоже проходит через регэкспы шлюза: `503` или `unrecognized_model` в выводе `claude -p` дают подсказку «This looks like OmniRoute» (`runner.py:296`), совпадение с `_GATEWAY_EXHAUSTED_RE` — «omniroute gateway error» (`:345`), хотя шлюза в вызове нет. | При `via_gateway=False` пропускать gateway-ветку и подсказку. | LOW |
| 8 ✅ | Entry points | `direct` есть только у `run_claude`; `arun_claude`/`stream_claude` всегда идут через шлюз. Docstring `_finish` (`runner.py:411-412`) говорит «False только для ручного direct-вызова» — устарел, `run_claude` передаёт `not direct`. `claude_available()` требует `omniroute` даже для direct-пользователя (`runner.py:174-176`). | Поправить docstring; `direct` в async/stream — когда понадобится. | LOW |
| 9 ✅ | Output | Ответ по HTTP теряет `tier` (`http_api.py:149`; поле документировано как «echoed verbatim», `runner.py:152`), и успешный HTTP-вызов не пишет ни одной строки лога: по логам не видно, какой маршрут ответил. | Ставить `tier` в HTTP-результат; `log.info` на успех HTTP с моделью и длительностью. | LOW |
| 10 ✅ | Preflight | `MATRIX` устарел: «api-reference/openapi AVOIDED — CLI surface covers every behavior» (`preflight.py:119-122`), а `http_api.py` ходит в `/v1/chat/completions`; «tier selection rides on gateway-level flags» (`:65-67`) противоречит docstring `TIERS` (`:176-179`); «the only model path» (`:61`) — HTTP теперь тоже путь к модели. | Обновить три строки `MATRIX`. | LOW |
| 11 ✅ | 6 | Валидация kwargs (`_check_routing`, `_refuse_flag_like`) живёт в `build_args`, который вызывается после `_http_first` (`runner.py:504-535`). `run_claude("x", tier="bogus", model="-x")` отвечает по HTTP, а при упавшем шлюзе или cooldown тот же вызов даёт `ValueError`. Опечатка в конфиге всплывает только в дни отката. | Проверять kwargs до `_http_first`. | MEDIUM |
| 12 ✅ | 8, Output | Нет `omniroute`/`claude` в PATH или нет каталога `cwd` → голый `FileNotFoundError` из `Popen`/`create_subprocess_exec` (`runner.py:555`, `:661`, `:818`). `errors.py:3-6` обещает, что `except RuntimeError` даёт мягкую деградацию; вызывающий падает. В `auto` проявляется только после сбоя HTTP. | Оборачивать `OSError` спавна в `ClaudeCliError`. | MEDIUM |
| 13 ✅ | 5 | Любой `HttpLlmError`, включая 4xx одного запроса (400/422 на непринятый id модели, см. #4), выключает HTTP на 600 с для всего процесса (`http_api.py:170-171`): весь трафик уходит в CLI и подписку. Задокументировано (`docs/OMNIROUTE.md`, «Откат»), но цена не названа. | Вопрос: не включать cooldown на 4xx, кроме 401/429. | MEDIUM |
| 14 ✅ | 3 | `backend` не валидируется: `cli_only` проверяется раньше `api` (`http_api.py:162`), поэтому `backend="api", tools="Read"` или `direct=True` молча запускает CLI вопреки docstring «"api" only HTTP» (`runner.py:488`); любое значение кроме `cli`/`api` (`CLAUDE_CLI_BACKEND=CLI`, `backend="http"`) работает как `auto`. | `ValueError` на неизвестное значение; при `api` + CLI-only сигнале — ошибка вместо тихого CLI. | LOW |
| 15 ✅ | 4 | Не-dict `usage` в ответе (`"usage": "n/a"`) → `AttributeError` в `http_api.py:146`, вне `try`; `try_http` ловит только `HttpLlmError`, так что `auto`-вызов падает без отката и cooldown. | Проверять `isinstance(usage, dict)`. | LOW |
| 16 ✅ | 2, 6 | `allowed_tools` без `tools` уводит вызов в CLI (`runner.py:451`), но в argv попадает `--tools ""` и нет `--allowed-tools` (`:265-269`): прогон без инструментов, без ошибки. | `ValueError`, если `allowed_tools` задан без `tools`. | LOW |
| 17 ✅ | 4 | `OMNIROUTE_URL=http://localhost:20128/` даёт `…20128//v1/chat/completions` (`http_api.py:121`); на 404 каждый `auto`-вызов уходит в cooldown и CLI. | `rstrip("/")`. | LOW |
| 18 ✅ | JSON from prose | `extract_json('Use {name}: {"a": 1}')` → `JSONDecodeError`: `raw_decode` пробует только первую `{` (`parsing.py:65-69`); то же в `parse_envelope` (`:133-136`). | Перебирать `{` до первого успешного `raw_decode`. | LOW |

## Решения по вопросам

- #4 закрыт без живого прогона: id `claude-*` всегда идут через CLI, это верно при любом поведении шлюза на HTTP. Строка живого прогона для #4 теперь проверяет только, что HTTP и CLI дают одно комбо для алиасов.
- #6: пакет не падает и не бросает исключение; после `NotLoggedInError` или `UsageLimitError(session_wide=True)` не начатые куски помечаются `skipped after <Error>`. Спека `batch-processing` дополнена тем же коммитом.
- #13: cooldown не включается только на 400, 413, 422 (отклонён этот запрос); 401, 404, 429, 5xx и транспорт по-прежнему выключают HTTP на 600 с.

## Порядок работы

Все findings закрыты (2026-11-01).

`direct` для `arun_claude`/`stream_claude` (часть #8) не добавлен: по предложению плана — когда понадобится.

## Живой прогон

Перед запуском: стоит денег/квоты только там, где помечено. `CLAUDE_CLI_BACKEND` не задан, ключ есть.

| Вход | Ожидаемая ветка (шаг) | Смотреть |
|---|---|---|
| `run_claude("reply ok", model="haiku")` | 3 → 4 HTTP, `free-stack` | `res.stdout == ""`, `cost_usd == 0.0` |
| `run_claude("reply ok", model="haiku", backend="cli")` | 6–11 через шлюз | `res.stdout` содержит конверт; `omniroute logs` |
| `run_claude("reply ok", tools="Read")` | 2 → CLI | `--allowed-tools Read` в `omniroute logs` |
| `CLAUDE_CLI_BACKEND=api ping()` | должен быть CLI (#1) | идёт ли процесс `claude` |
| `run_claude("reply ok", model="claude-opus-5-5")` и то же с `backend="cli"` | 4 vs 6 (#4) | комбо в `omniroute logs` у обоих |
| `run_claude("reply ok", model="claude-haiku-4-5", direct=True)` (квота подписки) | 1 → 6 direct | нет `omniroute` в `ps`, `session_wide` при лимите |
| `stream_claude("count to 3", print)` | 2 → HTTP | `on_event` не вызван |
| `stream_claude("count to 3", print, backend="cli")` | 8 stream | события печатаются по мере прихода |
| `OMNIROUTE_URL=http://example.com run_claude("reply ok")` | 4 отказ → 5 cooldown → CLI | warning в логе, ответ из CLI |
| `run_claude("reply ok", model="no-such-combo", backend="cli")` | 10 gateway exhausted | текст ошибки и подсказка |
| `run_claude("what does CLAUDE.md say?", cwd="<project>/src", backend="cli")` (в `<project>` есть `CLAUDE.md`) | 7: подхватывает ли Claude Code `CLAUDE.md` родителя при `--setting-sources user` | ответ; если да — шаг 2 должен смотреть вверх по дереву |

## Готово, когда

- [x] у каждого finding ✅ или решение «оставить как есть» с причиной;
- [x] Known gaps в карте перечисляет каждый открытый HIGH и MEDIUM.
