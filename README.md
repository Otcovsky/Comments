# Комментарии

Приложение комментариев с ветками ответов, вложениями и живыми обновлениями.

| Папка | Что внутри |
|---|---|
| `frontend/` | Клиент на React и Vite, конфигурация nginx, Dockerfile |
| `backend/` | Сервер на Django: GraphQL, WebSocket, вложения |
| `docs/api.md` | Контракт API: GraphQL, WebSocket, правила для вложений |
| `docs/db_schema.sql` | Схема базы данных |
| `perf/` | Нагрузочные тесты на k6, см. `perf/README.md` |

## Запуск фронтенда

```bash
cd frontend
npm ci
npm run dev
```

Клиент открывается на `http://localhost:5173`. Запросы к `/graphql/`, `/ws/` и `/media/` Vite передаёт на адрес из `BACKEND_URL`, по умолчанию `http://127.0.0.1:8000`.

## Бэкенд

Один процесс Daphne: группы Channels, видимые зоны клиентов и капчи живут в его памяти, поэтому запускать несколько копий нельзя без брокера. База и файлы блокируют поток, так что вся такая работа идёт через постоянные потоки из `backend/comments/pools.py`.

Настройки приходят из окружения: `DB_*` и `S3_*` как в `docs/api.md`, а также `DJANGO_SECRET_KEY` и `DJANGO_ALLOWED_HOSTS` (через запятую, для проверки Origin у сокета). Только на время нагрузочных тестов задают `CAPTCHA_TEST_ANSWER`, `RATE_LIMIT_EXEMPT` и `DB_POOL_REPORT`, см. `perf/README.md`.

## Тесты

Тестам нужна своя база, а пользователю облачной MySQL её создавать нельзя, поэтому они поднимают временную MySQL в контейнере:

```bash
docker compose -f docker-compose.test.yml run --rm --build tests
```

## Запуск на сервере

Рядом с папкой проекта лежит `comments.env`. Из папки проекта:

```bash
docker compose --env-file ../comments.env up -d --build
```

Миграции применяются при старте бэкенда. Снаружи открыт только порт 80, порт бэкенда закрыт.
