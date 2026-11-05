# Комментарии

Приложение комментариев с ветками ответов, вложениями и живыми обновлениями.

| Папка | Что внутри |
|---|---|
| `frontend/` | Клиент на React и Vite, конфигурация nginx, Dockerfile |
| `docs/api.md` | Контракт API: GraphQL, WebSocket, правила для вложений |
| `docs/db_schema.sql` | Схема базы данных |

Бэкенд на Django ещё не написан. Он должен реализовать контракт из `docs/api.md`.

## Запуск фронтенда

```bash
cd frontend
npm ci
npm run dev
```

Клиент открывается на `http://localhost:5173`. Запросы к `/graphql/`, `/ws/` и `/media/` Vite передаёт на адрес из `BACKEND_URL`, по умолчанию `http://127.0.0.1:8000`.
