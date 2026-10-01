# 2026-10-02: «omniroute висит на обоих комбо»

Разбор зависания вызовов `run_claude` ночью 2026-10-02 (примерно 02:55–03:55 +05). Симптом
проявился в chat-watch, но причина общая для всех потребителей `claude_cli`.

## Симптом

- `run_claude(...)` через `omniroute run claude -- -p …` не возвращался до таймаута вызывающего
  кода (40, 120, 200 с). Это касалось и комбо по умолчанию, и `profile="free-tier"`, и даже
  промпта `Say OK.` без инструментов.
- В `--output-format stream-json` последнее событие всегда одно и то же:
  `{"type":"system","subtype":"status","status":"requesting"}`. Ни `assistant`, ни `result`,
  ни ошибки.
- `CLAUDE_CODE_MAX_RETRIES=1` картину не изменил: за 100 с ответа нет. Значит, это не
  ретраи Claude Code с backoff (см. OMNIROUTE.md), а запрос, который сам не получает ответа.
- `omniroute health` при этом отвечает `healthy`, `omniroute logs` пустой.

## Что было не причиной

- **Сервер OmniRoute жив.** Процесс слушает `127.0.0.1:20128`, `/api/health` отвечает `200`
  за 3 мс, `/v1/models` без ключа отвечает `401` за 2 мс.
- **Права инструментов.** Запрещённый путь при `--allowed-tools "Read(//файл)"` Claude Code
  отклоняет сразу: tool_result `is_error: "Claude requested permissions to read from …, but you
  haven't granted it yet."`, после чего модель отвечает. Вне шлюза весь прогон занял 5.2 с.
  Docstring `runner.py` («auto-denies … would stall or fail») тут верен в первой части:
  отказ мгновенный. Зависания процесса из-за отказа нет, страдает только сама задача.

## Причины

### 1. Апстримы недоступны из-за сети/DNS машины (главная)

Журнал сервера `~/.omniroute/logs/application/app.log` (а не `omniroute logs`) показал, что
каждый таргет комбо падает по таймауту соединения:

- `fetch failed (cause: UND_ERR_CONNECT_TIMEOUT: Connect Timeout Error (attempted address:
  generativelanguage.googleapis.com:443, timeout: 10000ms))`, то же для `api.kilo.ai:443`;
- `[Antigravity] Network/Fetch Error - URL: https://cloudcode-pa.googleapis.com/…`,
  `Direct response did not start within 30000ms — retrying on a fresh socket`;
- `Fetch timeout after 600000ms on https://generativelanguage.googleapis.com/…` и
  `…cli-chat-proxy.grok.com/v1/responses`: **один таргет может держать запрос 10 минут**;
- `[HealthCheck] Error refreshing Copilot token: {"errorType":"TimeoutError"}`.

С машины в тот же момент:

| Проверка | Результат |
|---|---|
| `curl https://api.anthropic.com/` (двойной стек) | `Resolving timed out after 10001 milliseconds` |
| `curl -4 https://api.anthropic.com/` | `404`, TLS за 25 мс |
| `curl -6 …` | AAAA есть (`2607:6bc0::10`), connect `rc=7`: IPv6-маршрута по умолчанию нет |
| `resolvectl query -t A --cache=no api.kilo.ai` | 11.2 с |
| `resolvectl query -t AAAA --cache=no api.kilo.ai` | таймаут 15 с |
| `curl https://api.github.com/` (после прогрева) | стабильно `dns=10.01s`, потом 200 |
| `ping` до IP api.anthropic.com | 0% потерь, 3 мс |
| `api.telegram.org`, `www.google.com` | норма (кеш или быстрый ответ) |

DNS на машине: stub `127.0.0.53` (systemd-resolved) → единственный upstream `192.168.0.1`
(домашний роутер, Wi-Fi `wlp8s0`). Через какое-то время роутер отвечал на `dig` за 1–3 мс:
записи прогрелись. `api.github.com` при этом всё равно резолвился в curl ровно за 10.01 с,
то есть дважды по 5 с ожидания. Похоже на потерю одного из параллельных A/AAAA-запросов.
Точную механику (роутер, resolved, glibc) **не подтвердил**.

Почему это превращается в «висит» на стороне клиента. Комбо перебирает таргеты по очереди:
10 с connect-таймаута, 30 с «response did not start» с повтором на новом сокете, у отдельных
таргетов fetch-таймаут 600 с. Пока таргеты не кончились, клиенту не уходит ни байта, и
Claude Code остаётся в `status: requesting`.

Тот же сбой виден в chat-watch: `bot getUpdates failed: URLError: Temporary failure in name
resolution` в 02:52, 02:57, 03:02 и 03:06, при том что сессия Telethon (MTProto, подключение по IP)
работала.

### 2. Истекла авторизация провайдера `claude` в OmniRoute (отдельно, тоже чинить)

`"[claude] All 1 connection(s) authentication expired — please reconnect in the dashboard"`
в журнале 16 раз, плюс `All models failed | claude/claude-opus-5: auth — … (HTTP 401)`.
Комбо, где первым стоит `claude/*` (например `sub-first`: `Trying model 1/4:
claude/claude-sonnet-5`), каждый раз сначала тратят попытку на мёртвое подключение. Лечится
только переподключением в дашборде OmniRoute, ожидание не поможет.

## Как восстановилось

После прогрева DNS (около 03:55) оба маршрута ответили:
`default` (`run_claude("Say OK.", model="haiku")`) за 31.1 с, `profile="free-tier"` за 5.5 с.
31 с у default — это проход по таргетам комбо: в журнале `Combo "claude-haiku-4-5-20251001"
[priority] with 10 models` → `Trying model 1/8: antigravity/claude-sonnet-4-6` → … →
`Trying model 3/8: gemini/gemini-3-flash-preview`.

## Выводы и что делать

1. **Смотреть в серверный журнал, а не в `omniroute logs`.** Причина сбоя таргетов лежит в
   `~/.omniroute/logs/application/app.log` (JSON-строки, поля `msg`/`message`). Полезный фильтр:
   `grep -oE '"msg":"(Trying model|All models failed|Combo )[^"]*"'`. В этом файле есть
   идентификаторы подключений; секреты при выдержках маскировать.
2. **Сначала сеть машины.** `curl -4` против обычного `curl` до апстрима сразу отделяет
   DNS/IPv6 от шлюза. `ping` здесь ничего не доказывает: ICMP проходил всё время.
3. **DNS:** добавить второй резолвер в systemd-resolved (например `FallbackDNS=` или `DNS=` на
   Wi-Fi-линке) или выяснить, почему роутер теряет запросы. Исправлять ли, решает владелец машины.
4. **OmniRoute:** переподключить провайдера `claude` в дашборде. Если неработающий таргет должен
   быстрее отдавать ход следующему, уменьшить его fetch-таймаут (600 с) на стороне шлюза.
5. **Клиенты `claude_cli`:** таймаут вызывающего кода должен покрывать нормальный проход по комбо
   (31 с наблюдали на здоровой сети) и при этом срабатывать раньше 600-секундного таргета.
   В chat-watch запасной маршрут — это другое комбо (`LLM_ROUTES`: default → free-tier) с
   10-минутной паузой для упавшего маршрута. Прямой `claude` мимо шлюза не используется
   по правилу проекта.
