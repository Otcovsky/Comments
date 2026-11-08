FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# mysqlclient is compiled against the MySQL/MariaDB client library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc pkg-config default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# WEB_CONCURRENCY processes (docker-compose.yml); what they share lives in Redis
# (comments/shared.py). Migrations run once, in the migrate service, not in every process.
# Channels has no lifespan handler. Open WebSockets would hold a shutdown until Docker kills it.
# nginx already logs every request; uvicorn's own line per response cost 2.5% of the CPU.
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "off", "--timeout-graceful-shutdown", "5", "--no-access-log"]
