# План: claude-cli

Открытые баги, планы и идеи этого репо, 2026-11-05. Закрытый пункт удаляется или получает ✅ со ссылкой на коммит.

## Баги

Открытых findings карты логики нет: #1–24 закрыты ([план](docs/plans/2026-11-01-logic-map-fixes.md)). Новые findings дописываются туда с следующим номером.

Риски из system-design ([docs/system-design.md](docs/system-design.md), Risks):

- [ ] MEDIUM: одна ошибка HTTP (не по конкретному запросу) отключает HTTP для всех моделей на 600 с; 429 на `hard` гасит и `free-stack`, батч уходит на медленный CLI. `src/claude_cli/http_api.py:259-263`
- [ ] MEDIUM: `workers=N` запускает N одновременных процессов `claude` без лимита по памяти (~N x 150-300 MB, оценка). `src/claude_cli/batch.py:98-100`
- [x] ✅ 2026-11-05 WAL + busy timeout 30 с в `FileCacheStore`, не закоммичено. (было: параллельные воркеры теряли записи, "database is locked".) `src/claude_cli/cache.py`
- [x] ✅ 2026-11-05 `cached_run` считает ошибку чтения кэша промахом, не закоммичено. (было: битый файл ломал вызов.) `src/claude_cli/cache.py`
- [ ] LOW: истёкшие строки кэша (`max_age_days`) не удаляются, файл растёт (0.2-1 GB/год при 5000 вызовов/день). `src/claude_cli/cache.py:51-60`
- [ ] LOW: timeout действует на каждую фазу HTTP, а не на весь вызов; до CLI-fallback может пройти несколько `timeout`. `src/claude_cli/http_api.py:37-41`
- [ ] LOW: отменённая задача `arun_claude` оставляет HTTP-запрос работать в `to_thread`; при массовой отмене копятся потоки. `src/claude_cli/runner.py:895`
- [ ] LOW: `_down_until` — глобал без блокировки; гонка безвредна (одна лишняя проба). `src/claude_cli/http_api.py:41`

## Планы и фичи

- [ ] Живой прогон таблицы из плана (стоит квоты там, где помечено): [docs/plans/2026-11-01-logic-map-fixes.md](docs/plans/2026-11-01-logic-map-fixes.md), раздел «Живой прогон»; проверить в т.ч. подхватывает ли Claude Code `CLAUDE.md` родителя при `--setting-sources user` (шаг 2 смотрит вверх по дереву?)
- [ ] Метрики: число вызовов, доля HTTP/CLI, срабатывания cooldown (сейчас только INFO/WARNING в логе). [docs/system-design.md](docs/system-design.md), Risks

## Идеи

- [ ] `direct` для `arun_claude`/`stream_claude`: добавить, когда понадобится. [план](docs/plans/2026-11-01-logic-map-fixes.md), «Порядок работы»
- [ ] Кэш: WAL, затем Postgres/Redis, только если пишет больше одного процесса; триггер: "database is locked" в >1% `put` или файл >1 GB. [docs/system-design.md](docs/system-design.md), §8
- [ ] Cooldown по модели вместо общего, если в батче больше одного комбо. [docs/system-design.md](docs/system-design.md), §8
- [ ] Бэкофф с джиттером при 429 (сейчас весь cooldown батч идёт через CLI). [docs/system-design.md](docs/system-design.md), §6
- [ ] Состояние cooldown в объекте вместо глобалов (изоляция, тесты). [docs/system-design.md](docs/system-design.md), §7

Ступень сегодня: rung 1, держит 500-5000 вызовов/день; раньше упрётся OmniRoute и подписка.

## Подробные планы

- [docs/plans/2026-11-01-logic-map-fixes.md](docs/plans/2026-11-01-logic-map-fixes.md): findings карты логики и живой прогон
- [docs/system-design.md](docs/system-design.md): разбор нагрузки, отказы, риски (2026-11-05); сводка по всем репо: `~/dev/docs/system-design-report-2026-11-05.md`
- [docs/logic-map.md](docs/logic-map.md), [docs/logic-map.ru.md](docs/logic-map.ru.md): карта логики
- [docs/OMNIROUTE.md](docs/OMNIROUTE.md): сторона шлюза
- `openspec/specs/`: нормативное поведение
