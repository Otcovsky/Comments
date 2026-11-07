# Baseline: suite v1 (2026-11-03)

Начало цепочки оптимизаций: каждое следующее изменение сравнивается с предыдущим прогоном цепочки (`perf/chain.txt`), а весь путь от этих цифр — в [OPTIMIZATIONS.md](OPTIMIZATIONS.md) и в `python perf/compare.py --chain`. Прогон: `perf/results/2026-11-03_0823_baseline-v1/`.

Код: `main` @ `7141e51` плюс только инструменты замеров (`perf_reset`, `perf_db_status`), на сервере и локально коммит `1b17174`. Сервер `oracle-comments`: Ampere A1, 2 ядра arm64, 11 ГБ, один Docker Compose (nginx + daphne + MySQL 8.4). Нагрузка: `loadtest` (та же конфигурация) в той же подсети, k6 v2.3.0. Данные: `seed_perf --top 10000 --replies 50000 --authors 2000 --seed 42`, у 10% комментариев есть вложение, в конце `ANALYZE TABLE`. Прошлый набор и выводы о узких местах — в `BASELINE-v0.md`.

## Главное

- **Ёмкость: 12 итераций/с** (около 13,6 запроса к API в секунду) при SLO p95 < 500 мс, только слушатели WebSocket. На 14 система обваливается: p95 = 3,9 с. Совпадает с прошлым набором (12), то есть новые проверки сами по себе нагрузку не меняют.
- **10 открытых вкладок при 4 итерациях/с — уже за пределами SLO: p95 = 2,2 с.** На каждый новый комментарий (около 0,5 в секунду) все 10 вкладок разом запрашивают список: ~5 перезагрузок в секунду, больше, чем весь пользовательский трафик списка (2,4 в секунду). База занята на ~77% (154% из 200%), запрос списка пользователя, попавший на такую пачку, ждёт до 2,6 с (`list p95`), а новый комментарий появляется у других через 1,5–1,7 с в среднем и до 3,2 с (`visible`). Иногда k6 не успевает запустить итерацию (`dropped iterations` 2–3 за прогон).
- **Данные всегда свежие и правильные:** `content ok`, `fresh list`, `fresh thread` и `attachment ready` — 100%. Любая оптимизация должна их сохранить.
- **Загрузка вложений** (`upload`, 2 итерации/с): attach p95 ≈ 114 мс, файл доступен сразу после ответа (p95 2 мс), база почти не занята (3% CPU), backend ~10%.
- **Генератор не упирается:** CPU машины нагрузки свободен на 98%.
- **Разброс `load-a` против `load-b`:** общие p50/p95/p99, CPU и счётчики MySQL расходятся на 0–3%. У редких запросов (`captcha`, `attach`, `reload-thread`) p95 скачет на 10–35%.

Главная цель оптимизаций видна сразу: тяжёлый запрос списка (~200 мс, ~70–100 тысяч прочитанных строк на запрос) и то, что каждая вкладка повторяет его на каждое событие.

## load: 4 итерации/с, 5 минут, 50 WS-подключений, из них 10 вкладок

| metric | load-a | load-b | change |
|---|---|---|---|
| SLO passed | NO | NO |  |
| foreign requests | 0 | 0 |  |
| rate, it/s | 4.00 | 4.00 |  |
| requests (main) | 1350 | 1345 |  |
| tab reloads (live) | 1477 | 1466 |  |
| failed, % | 0.00 | 0.00 |  |
| dropped iterations | 2 | 3 | +50% worse |
| all p50, ms | 200 | 199 | -0% |
| all p95, ms | 2272 | 2241 | -1% |
| all p99, ms | 3062 | 3165 | +3% |
| list p95, ms | 2571 | 2743 | +7% worse |
| thread p95, ms | 63.5 | 68.2 | +7% worse |
| captcha p95, ms | 72.9 | 97.7 | +34% worse |
| create p95, ms | 151 | 144 | -5% |
| preview p95, ms | 35.8 | 32.8 | -8% better |
| attach p95, ms | 300 | 261 | -13% better |
| reload p95, ms | 2841 | 2975 | +5% |
| reload-thread p95, ms | 119 | 78.6 | -34% better |
| media p95, ms | 4.87 | 3.85 | -21% better |
| content ok, % | 100 | 100 | +0% |
| fresh list, % | 100 | 100 | +0% |
| fresh thread, % | 100 | 100 | +0% |
| visible p50, ms | 1729 | 1490 | -14% better |
| visible p95, ms | 3204 | 2941 | -8% better |
| attachment ready, % | 100 | 100 | +0% |
| attachment ready p95, ms | 10.7 | 10.4 | -3% |
| media files | 250 | 244 |  |
| media failed, % | 0.00 | 0.00 |  |
| media MiB | 2.60 | 3.63 |  |
| ws delivery p50, ms | 49.0 | 48.0 | -2% |
| ws delivery p95, ms | 186 | 139 | -25% better |
| ws delivered, % | 100 | 100 | +0% |
| app CPU avg, % (all services) | 167 | 166 | -0% |
| backend CPU avg, % | 12.1 | 12.1 | +0% |
| backend CPU max, % | 31.1 | 29.6 | -5% |
| db CPU avg, % | 154 | 153 | -1% |
| nginx CPU avg, % | 0.78 | 0.75 | -4% |
| backend mem max, MiB | 87.9 | 88.4 | +1% |
| db mem max, MiB | 1977 | 1946 | -2% |
| host CPU idle, % | 14.5 | 14.3 | -1% |
| host iowait, % | 0.03 | 0.03 | +0% |
| host disk read, KiB/s | 47.2 | 47.9 | +1% |
| host disk write, KiB/s | 1102 | 1191 | +8% worse |
| load machine CPU idle, % | 98.7 | 98.4 |  |
| SQL queries / request | 14.9 | 14.9 | +0% |
| rows read / request | 100705 | 100551 | -0% |
| full scans / request | 0.77 | 0.77 | -0% |
| tmp disk tables | 48 | 49 | +2% |
| slow queries | 0 | 0 |  |

## upload: только загрузки, 2 итерации/с, 3 минуты

| metric | upload |
|---|---|
| SLO passed | yes |
| foreign requests | 0 |
| rate, it/s | 2.00 |
| requests (main) | 722 |
| tab reloads (live) | 0 |
| failed, % | 0.00 |
| dropped iterations | 0 |
| all p50, ms | 22.0 |
| all p95, ms | 110 |
| all p99, ms | 118 |
| captcha p95, ms | 18.5 |
| attach p95, ms | 114 |
| media p95, ms | 1.49 |
| content ok, % | 100 |
| attachment ready, % | 100 |
| attachment ready p95, ms | 2.00 |
| media files | 361 |
| media failed, % | 0.00 |
| media MiB | 0.04 |
| app CPU avg, % (all services) | 13.7 |
| backend CPU avg, % | 10.5 |
| backend CPU max, % | 22.5 |
| db CPU avg, % | 2.87 |
| nginx CPU avg, % | 0.38 |
| backend mem max, MiB | 76.5 |
| db mem max, MiB | 1770 |
| host CPU idle, % | 89.7 |
| host iowait, % | 0.98 |
| host disk read, KiB/s | 29.5 |
| host disk write, KiB/s | 997 |
| load machine CPU idle, % | 98.9 |
| SQL queries / request | 15.1 |
| rows read / request | 2616 |
| full scans / request | 0.02 |
| tmp disk tables | 0 |
| slow queries | 0 |

## Ёмкость: step, по 60 с на частоту, без вкладок

| rate | SLO | req/s | p50 | p95 | p99 | failed % | dropped | app CPU | db CPU | fresh % | ws p95 | k6 idle |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4 | ok | 4.55 | 193 | 224 | 259 | 0.00 | 0 | 53.2 | 46.3 | - | 158 | 99.1 |
| 6 | ok | 6.80 | 194 | 220 | 237 | 0.00 | 0 | 78.3 | 68.6 | - | 146 | 98.6 |
| 8 | ok | 9.07 | 196 | 225 | 246 | 0.00 | 0 | 107 | 92.4 | - | 147 | 98.6 |
| 10 | ok | 11.3 | 201 | 246 | 290 | 0.00 | 0 | 127 | 112 | - | 183 | 98.5 |
| 12 | ok | 13.6 | 207 | 273 | 350 | 0.00 | 0 | 163 | 143 | - | 175 | 98.2 |
| 14 | FAIL | 15.5 | 765 | 3850 | 4500 | 0.11 | 17 | 194 | 173 | - | 233 | 97.9 |

capacity: 12 it/s within the SLO
