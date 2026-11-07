#!/bin/sh
# Runs on the app server; perf/run.sh copies it there and calls it over ssh.
# Usage: APP_DIR=~/comments app.sh check|info|reset|stats-start|stats-stop|db-status|foreign
set -eu

cd "$APP_DIR"
OUT=/tmp/perf
mkdir -p "$OUT"
# The settings live next to the project directory (README.md of the repository).
ENV_FILE=${APP_ENV_FILE:-../comments.env}
compose() { docker compose --env-file "$ENV_FILE" "$@"; }
# Answers once the backend and the database work, without touching the data.
PING='{"query":"{ comments(page: 1, pageSize: 1, ordering: CREATED_AT_DESC) { count } }"}'

case "$1" in
check)
    # The test mode of perf/README.md: a known captcha answer, and no limits per IP for the
    # load machine in the backend and in nginx. Without them most writes are refused.
    answer=$(compose exec -T backend printenv CAPTCHA_TEST_ANSWER || true)
    if [ -z "$answer" ]; then
        echo "CAPTCHA_TEST_ANSWER is not set in $ENV_FILE (see perf/README.md)" >&2
        exit 1
    fi
    for service in backend frontend; do
        exempt=$(compose exec -T "$service" printenv RATE_LIMIT_EXEMPT || true)
        if [ -z "$exempt" ] || [ "$exempt" = 127.0.0.1/32 ]; then
            echo "RATE_LIMIT_EXEMPT is not set for $service (see perf/README.md)" >&2
            exit 1
        fi
    done
    echo "$answer"
    ;;
info)
    # The server gets the code without git; the deploy writes the commit to REVISION.
    if git rev-parse HEAD >/dev/null 2>&1; then
        sha=$(git rev-parse HEAD)
        dirty=$(git status --porcelain | wc -l)
    else
        sha=$(cat REVISION 2>/dev/null || echo unknown)
        dirty=null
    fi
    printf '{"git_sha": "%s", "git_dirty_files": %s, "images": %s}\n' \
        "$sha" "$dirty" "$(compose images --format json 2>/dev/null || echo '[]')"
    ;;
reset)
    shift
    # Same data before every run, then fresh processes of every service except the database
    # (its data was just replaced, and a cloud database has no service). The restart also
    # clears what the backend keeps in memory: captchas, limits and viewports.
    compose exec -T backend python manage.py perf_reset --noinput "$@"
    # Files nginx kept from the bucket survive a restart, and a run must not start warm.
    compose exec -T frontend sh -c 'rm -rf /var/cache/nginx/media/*'
    compose restart $(compose config --services | grep -vx db) >/dev/null
    # A broker starts slower than the backend, and a task sent before it is up is lost.
    if compose config --services | grep -qx broker; then
        healthy=
        for _ in $(seq 60); do
            if [ "$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q broker)")" = healthy ]; then
                healthy=1
                break
            fi
            sleep 1
        done
        if [ -z "$healthy" ]; then
            echo "broker did not come back after restart" >&2
            exit 1
        fi
    fi
    # Every backend replica must answer, not just the first one: replicas take turns at
    # startup, and WebSocket clients that connect meanwhile would all stay on the early ones.
    replicas=$(compose ps -q backend | wc -l)
    for index in $(seq "$replicas"); do
        ready=
        for _ in $(seq 60); do
            if compose exec -T --index "$index" backend python -c "
import sys, urllib.request
request = urllib.request.Request('http://127.0.0.1:8000/graphql/', sys.argv[1].encode(), {'Content-Type': 'application/json'})
sys.exit(b'\"errors\"' in urllib.request.urlopen(request, timeout=5).read())
" "$PING" >/dev/null 2>&1; then
                ready=1
                break
            fi
            sleep 1
        done
        if [ -z "$ready" ]; then
            echo "backend replica $index did not come back after restart" >&2
            exit 1
        fi
    done
    # nginx re-resolves the backend every 10 s; let it see all replicas before the run.
    sleep 11
    for _ in $(seq 60); do
        if curl -sf -H 'Content-Type: application/json' -d "$PING" \
            http://localhost:"${APP_PORT:-80}"/graphql/ 2>/dev/null | grep -q '"data"'; then
            exit 0
        fi
        sleep 1
    done
    echo "nginx does not reach the backend after restart" >&2
    exit 1
    ;;
stats-start)
    rm -f "$OUT"/docker_stats.csv "$OUT"/vmstat.txt
    # Container name -> compose service, so replicas and new services are summed correctly.
    compose ps --format '{{.Name}},{{.Service}}' > "$OUT"/services.csv
    echo "ts,name,cpu,mem" > "$OUT"/docker_stats.csv
    # docker stats --no-stream takes about two seconds, which is the sampling period.
    nohup sh -c "while :; do docker stats --no-stream --format \"\$(date +%s),{{.Name}},{{.CPUPerc}},{{.MemUsage}}\"; done" \
        >> "$OUT"/docker_stats.csv 2>/dev/null &
    echo $! > "$OUT"/stats.pid
    TZ=UTC nohup vmstat -n -t 2 > "$OUT"/vmstat.txt 2>/dev/null &
    echo $! > "$OUT"/vmstat.pid
    ;;
stats-stop)
    for name in stats vmstat; do
        if [ -f "$OUT/$name.pid" ]; then
            pid=$(cat "$OUT/$name.pid")
            pkill -P "$pid" 2>/dev/null || true
            kill "$pid" 2>/dev/null || true
            rm -f "$OUT/$name.pid"
        fi
    done
    ;;
foreign)
    # Requests from outside the private network since unix time $2: an open browser tab
    # reloads the list on new comments and quietly adds load to the run.
    compose logs --no-log-prefix --since "$2" frontend 2>/dev/null \
        | awk '$1 ~ /^[0-9.]+$/ && $1 !~ /^(10|127)\./ && $1 !~ /^172\.(1[6-9]|2[0-9]|3[01])\./ && $1 !~ /^192\.168\./ {print $1}' \
        | sort | uniq -c | sort -rn | awk '{total += $1; detail = detail " " $2 "=" $1} END {print total + 0 detail}'
    ;;
db-status)
    # Through the app's own connection: works for the local container and a cloud database.
    compose exec -T backend python manage.py perf_db_status
    ;;
*)
    echo "unknown command $1" >&2
    exit 2
    ;;
esac
