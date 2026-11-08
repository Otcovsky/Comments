import ipaddress
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
# Nginx is the only way in, so the list only guards the WebSocket Origin check.
ALLOWED_HOSTS = [host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if host]

INSTALLED_APPS = ["comments"]
# The API uses no cookies or sessions, so CSRF and the like have nothing to protect.
MIDDLEWARE = []

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ["DB_HOST"],
        "PORT": os.environ["DB_PORT"],
        # Connections belong to the threads in comments/pools.py, which never exit.
        "CONN_MAX_AGE": 600,
        "CONN_HEALTH_CHECKS": True,
        # The client library does not encrypt unless told to. The server's certificate is
        # self-signed, so it is not verified.
        "OPTIONS": {"charset": "utf8mb4", "connect_timeout": 3, "ssl_mode": "REQUIRED"},
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# What every backend process must see the same way: captchas, limits per IP, viewports,
# the count of top-level comments (comments/shared.py) and the channel layer.
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [REDIS_URL]}}}
# Redis outlives a test run, so the runner empties it first.
TEST_RUNNER = "comments.tests.runner.Runner"

USE_TZ = True
TIME_ZONE = "UTC"

# Threads of comments/pools.py. DB threads hold one connection each; file threads decode images.
DB_THREADS = 8
FILE_THREADS = 2
# Load tests only: every this many seconds the log gets how long calls waited for a DB thread
# and how long they ran (comments.pools.PoolStats). 0 turns it off.
DB_POOL_REPORT = int(os.environ.get("DB_POOL_REPORT", "0"))

# Seconds a token from a solved captcha replaces the next captchas.
AUTHOR_TOKEN_TTL = 3600

# Load tests only, see perf/README.md; never set for real users. Every captcha gets this
# answer, and clients from this network (the load machine) skip the limits per IP.
CAPTCHA_TEST_ANSWER = os.environ.get("CAPTCHA_TEST_ANSWER", "")
RATE_LIMIT_EXEMPT = ipaddress.ip_network(os.environ["RATE_LIMIT_EXEMPT"]) if os.environ.get("RATE_LIMIT_EXEMPT") else None

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_REGION = os.environ.get("S3_REGION", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
