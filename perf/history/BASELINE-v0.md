# Baseline v0: до интеграций (2026-11-02)

> Снят старым набором (до suite v1): вкладки только слушали WebSocket, ветки открывались по случайному id. Для сравнения новых изменений используйте `BASELINE.md` (suite v1); здесь остаются выводы о том, где узкие места.

Код: `main` @ `4168f61` плюс только инструменты для замеров (`seed_perf`, `CAPTCHA_TEST_ANSWER`). Сервер `oracle-comments`: Ampere A1, 2 ядра arm64, 11 ГБ, один Docker Compose (nginx + daphne + MySQL 8.4). Нагрузка: `loadtest` (та же конфигурация) в той же подсети, k6 v2.3.0. Данные: `seed_perf --top 10000 --replies 50000 --authors 2000 --seed 42`, у 10% комментариев есть вложение, в конце `ANALYZE TABLE`.

## Главное

- **Ёмкость: 12 итераций/с (около 13,5 HTTP-запроса к API в секунду)** при SLO p95 < 500 мс. До 12 итераций/с p95 почти не меняется (220–265 мс), на 14 система обваливается: p95 = 4,6 с, k6 не успевает запускать итерации.
- **Узкое место — MySQL на запросе списка.** При 8 итерациях/с MySQL занимает ~93% CPU, daphne ~12%, nginx меньше 1%. Около 70 тысяч прочитанных строк на один запрос к API.
- **`GET /api/comments/` — около 195 мс даже без нагрузки**, из них ~150 мс в самой базе. Запрос страницы проходит по всем 10 000 корневым комментариям ради `reply_count` и только потом сортирует (`EXPLAIN`: `Using temporary; Using filesort`, индекс по `created_at` не используется). `COUNT` для пагинации добавляет ещё десятки миллисекунд.
- **План этого запроса неустойчив.** Пока статистика InnoDB устарела (например, сразу после массовой замены данных), MySQL выбирает другой план, начиная с полного прохода по авторам, — вдвое медленнее, ~290 мс. С таким планом ёмкость была всего 4 итерации/с. Поэтому `seed_perf` делает `ANALYZE TABLE`; на живом сайте риск тот же после больших изменений данных.
- Ветка (`thread`) ~30 мс, создание комментария ~70 мс, вложение ~110 мс без конкуренции за базу (160–190 мс в общей смеси).
- **Чтение вложений** (nginx с тома): p95 ≈ 3 мс, около 1000 файлов (~11 МБ) за 5 минут при 8 итерациях/с. Файлы читаются из кэша ОС: `host disk read` ≈ 0.
- **Запись вложений** (`upload`, 2 итерации/с): ~1 МБ/с записи на диск, iowait ~1%, daphne ~10% CPU, MySQL ~3%. nginx при этом пишет тело каждой загрузки больше 16 КБ во временный файл (`client request body is buffered to a temporary file`).
- **WebSocket:** доставка 50 клиентам, p50 = 40 мс, p95 ≈ 100–130 мс (от отправки POST), без потерь.
- Django открывает соединение с MySQL на каждый запрос (`CONN_MAX_AGE = 0`): ~14 команд MySQL на запрос к API, из них полезных 2–4, остальное — `SET` при подключении.
- При создании ответа лишний `SELECT` корня ветки (`parent.root`, хотя хватило бы `parent.root_id`), см. `QueryBudgetTests`.

**Не измерено, но важно для Events и Cache.** Фронтенд на каждое событие `comment.created` перезагружает текущую страницу списка (и открытые ветки). Каждая открытая вкладка умножает самый тяжёлый запрос на поток новых комментариев. При ~50 вкладках и одном комментарии в 2 секунды это ~25 запросов списка в секунду — вдвое больше всей ёмкости. В тесте WS-клиенты пока только слушают; одна открытая вкладка во время прогона заметно искажала результаты, поэтому `run.sh` теперь предупреждает о посторонних запросах (`foreign requests`).

## Основное сравнение: `load` при 8 итерациях/с (2/3 ёмкости), 5 минут, 50 WS-клиентов

С этими цифрами сравниваем все следующие изменения: `bash perf/run.sh load <метка> 8`.

| metric | 2026-11-02_1919_baseline-load8-a | 2026-11-02_1925_baseline-load8-b | change |
|---|---|---|---|
| SLO passed | yes | yes |  |
| foreign requests | 0 | 0 |  |
| rate, it/s | 8.00 | 8.00 |  |
| requests (main) | 2697 | 2696 |  |
| failed, % | 0.00 | 0.00 |  |
| dropped iterations | 0 | 0 |  |
| all p50, ms | 196 | 196 | +0% |
| all p95, ms | 226 | 228 | +1% |
| all p99, ms | 252 | 258 | +2% |
| list p95, ms | 233 | 236 | +1% |
| thread p95, ms | 31.9 | 32.7 | +3% |
| captcha p95, ms | 50.8 | 48.3 | -5% better |
| create p95, ms | 73.8 | 68.3 | -8% better |
| preview p95, ms | 35.4 | 28.5 | -19% better |
| attach p95, ms | 160 | 181 | +13% worse |
| media p95, ms | 2.79 | 2.88 | +3% |
| media files | 1070 | 935 |  |
| media failed, % | 0.00 | 0.00 |  |
| media MiB | 11.4 | 10.1 |  |
| ws delivery p50, ms | 40.0 | 41.0 | +2% |
| ws delivery p95, ms | 97.0 | 95.0 | -2% |
| ws delivered, % | 100 | 100 | +0% |
| backend CPU avg, % | 12.2 | 12.4 | +1% |
| backend CPU max, % | 25.6 | 30.1 | +17% worse |
| db CPU avg, % | 93.0 | 93.9 | +1% |
| nginx CPU avg, % | 0.87 | 0.89 | +2% |
| backend mem max, MiB | 72.3 | 71.3 | -1% |
| db mem max, MiB | 1693 | 1696 | +0% |
| host CPU idle, % | 43.1 | 43.7 | +1% |
| host iowait, % | 0.24 | 0.21 | -14% better |
| host disk read, KiB/s | 0.00 | 0.00 |  |
| host disk write, KiB/s | 502 | 512 | +2% |
| SQL queries / request | 13.8 | 13.8 | +0% |
| rows read / request | 69937 | 70296 | +1% |
| full scans / request | 0.54 | 0.54 | +1% |
| tmp disk tables | 0 | 0 |  |
| slow queries | 0 | 0 |  |

## Загрузка вложений: `upload` при 2 итерациях/с, 3 минуты

`bash perf/run.sh upload <метка> 2`. Только капча и POST с вложением (JPEG 60%, GIF 25%, TXT 15%), без WebSocket.

| metric | 2026-11-02_1911_baseline-upload-a | 2026-11-02_1915_baseline-upload-b | change |
|---|---|---|---|
| SLO passed | yes | yes |  |
| foreign requests | 0 | 1 | worse |
| rate, it/s | 2.00 | 2.00 |  |
| requests (main) | 720 | 722 |  |
| failed, % | 0.00 | 0.00 |  |
| dropped iterations | 0 | 0 |  |
| all p50, ms | 21.5 | 21.7 | +1% |
| all p95, ms | 112 | 110 | -2% |
| all p99, ms | 121 | 119 | -1% |
| captcha p95, ms | 18.7 | 18.7 | -0% |
| attach p95, ms | 117 | 113 | -3% |
| media files | 0 | 0 |  |
| media failed, % | 0.00 | 0.00 |  |
| media MiB | 0.06 | 0.00 |  |
| backend CPU avg, % | 9.20 | 10.3 | +12% worse |
| backend CPU max, % | 22.8 | 23.2 | +1% |
| db CPU avg, % | 3.35 | 3.33 | -0% |
| nginx CPU avg, % | 0.27 | 0.30 | +9% worse |
| backend mem max, MiB | 74.4 | 81.3 | +9% worse |
| db mem max, MiB | 1677 | 1678 | +0% |
| host CPU idle, % | 89.8 | 89.5 | -0% |
| host iowait, % | 0.97 | 0.98 | +1% |
| host disk read, KiB/s | 2.93 | 0.00 | -100% better |
| host disk write, KiB/s | 1023 | 1053 | +3% |
| SQL queries / request | 15.2 | 15.1 | -0% |
| rows read / request | 2105 | 2292 | +9% worse |
| full scans / request | 0.02 | 0.02 | +8% worse |
| tmp disk tables | 0 | 0 |  |
| slow queries | 0 | 0 |  |

## Лёгкая нагрузка: `load` при 4 итерациях/с

| metric | 2026-11-02_1859_baseline-load-a | 2026-11-02_1905_baseline-load-b | change |
|---|---|---|---|
| SLO passed | yes | yes |  |
| foreign requests | 0 | 1 | worse |
| rate, it/s | 4.00 | 4.00 |  |
| requests (main) | 1352 | 1352 |  |
| failed, % | 0.00 | 0.00 |  |
| dropped iterations | 0 | 0 |  |
| all p50, ms | 195 | 194 | -1% |
| all p95, ms | 221 | 221 | -0% |
| all p99, ms | 243 | 246 | +1% |
| list p95, ms | 228 | 226 | -1% |
| thread p95, ms | 28.5 | 27.5 | -3% |
| captcha p95, ms | 46.8 | 48.7 | +4% |
| create p95, ms | 72.9 | 68.8 | -6% better |
| preview p95, ms | 34.7 | 27.8 | -20% better |
| attach p95, ms | 162 | 189 | +16% worse |
| media p95, ms | 2.08 | 2.97 | +43% worse |
| media files | 540 | 550 |  |
| media failed, % | 0.00 | 0.00 |  |
| media MiB | 5.89 | 6.06 |  |
| ws delivery p50, ms | 41.0 | 40.0 | -2% |
| ws delivery p95, ms | 124 | 131 | +6% worse |
| ws delivered, % | 100 | 100 | +0% |
| backend CPU avg, % | 6.31 | 6.47 | +3% |
| backend CPU max, % | 21.3 | 21.4 | +0% |
| db CPU avg, % | 47.4 | 46.9 | -1% |
| nginx CPU avg, % | 0.43 | 0.43 | +0% |
| backend mem max, MiB | 70.0 | 69.4 | -1% |
| db mem max, MiB | 1682 | 1686 | +0% |
| host CPU idle, % | 69.7 | 70.2 | +1% |
| host iowait, % | 0.06 | 0.08 | +33% worse |
| host disk read, KiB/s | 11.5 | 4.53 | -60% better |
| host disk write, KiB/s | 342 | 335 | -2% |
| SQL queries / request | 13.7 | 13.7 | +0% |
| rows read / request | 69355 | 69099 | -0% |
| full scans / request | 0.53 | 0.53 | -0% |
| tmp disk tables | 0 | 0 |  |
| slow queries | 0 | 0 |  |

## Ёмкость: `step` от 2 до 40 с шагом 2, по 60 с

| rate | SLO | req/s | p50 | p95 | p99 | failed % | dropped | backend CPU | db CPU | ws p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | ok | 2.35 | 194 | 220 | 254 | 0.00 | 0 | 2.42 | 23.0 | 114 |
| 4 | ok | 4.60 | 195 | 223 | 254 | 0.00 | 0 | 5.63 | 45.7 | 141 |
| 6 | ok | 6.83 | 193 | 223 | 250 | 0.00 | 0 | 9.55 | 70.0 | 156 |
| 8 | ok | 9.05 | 194 | 224 | 251 | 0.00 | 0 | 11.4 | 89.2 | 155 |
| 10 | ok | 11.3 | 201 | 252 | 301 | 0.00 | 0 | 14.6 | 115 | 193 |
| 12 | ok | 13.5 | 204 | 264 | 319 | 0.00 | 0 | 17.5 | 140 | 206 |
| 14 | FAIL | 15.4 | 495 | 4649 | 5908 | 0.00 | 24 | 22.4 | 176 | 235 |

После оптимизаций ёмкость вырастет, поэтому шаг стоит увеличить: например, `bash perf/run.sh step after-cache 5 5 100`.

## Разброс

Общие p50/p95/p99, CPU и счётчики MySQL у одинаковых прогонов совпадают в пределах 1–4%. У редких запросов в основной смеси (`create`, `preview`, `attach`: 25–300 штук за прогон) p95 скачет на 10–40%, для них есть режим `upload`. `media p95` — единицы миллисекунд, поэтому разница в доли миллисекунды выглядит как десятки процентов.
