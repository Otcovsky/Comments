#!/usr/bin/env bash
# Runs a load test from this machine: resets the app server's state, starts resource
# sampling there and on the load machine, runs k6 and collects everything into perf/results.
#
#   perf/run.sh suite <label>                  # the fixed suite (suite.env): use it to compare
#   perf/run.sh smoke [label]                  # 30 s check that everything works
#   perf/run.sh load  <label> [rate]           # fixed rate, 5 minutes
#   perf/run.sh soak  <label> [rate]           # fixed rate, 30 minutes
#   perf/run.sh upload <label> [rate]          # only uploads with attachments, 3 minutes
#   perf/run.sh step  <label> [rate...]        # STEP_DURATION per rate until the SLO breaks
#   perf/run.sh day   <label> [rate]           # peak hour of 100k visitors a day (docs/scaling.md)
#
# Outside the suite, k6 variables can be overridden from the environment, e.g. DURATION=2m.
# RESET=0 keeps the data of the previous run: restoring a million comments takes a quarter
# of an hour and keeps the database busy for a while after.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/perf.env" ] || { echo "Create $HERE/perf.env from perf.env.example" >&2; exit 1; }
# shellcheck source=/dev/null
. "$HERE/perf.env"
# shellcheck source=suite.env
. "$HERE/suite.env"

MODE=${1:-}
LABEL=${2:-$MODE}
case "$MODE" in
suite | smoke | load | soak | step | upload | day) ;;
*) sed -n '2,13p' "$0" >&2; exit 2 ;;
esac

OVERRIDES=$(env | grep -E '^(DURATION|WARMUP|WS_CLIENTS|VIEWERS|WS_PER_VU|RATE|SLO_P95|SEED|USE_TOKEN|RESET)=' | tr '\n' ' ' || true)
if [ "$MODE" = suite ] && [ -n "$OVERRIDES" ]; then
    echo "The suite runs with fixed settings; unset: $OVERRIDES" >&2
    exit 2
fi
k6_version=$(grep -oE 'SUITE_VERSION = [0-9]+' "$HERE/k6/main.js" | grep -oE '[0-9]+$')
if [ "$k6_version" != "$SUITE_VERSION" ]; then
    echo "SUITE_VERSION is $SUITE_VERSION in suite.env but $k6_version in k6/main.js" >&2
    exit 2
fi

SSH_OPTS=(-o BatchMode=yes -o ServerAliveInterval=30)
app() { ssh "${SSH_OPTS[@]}" "$APP_SSH" "APP_DIR=$APP_DIR sh /tmp/perf-app.sh $*"; }
load() { ssh "${SSH_OPTS[@]}" "$LOAD_SSH" "$@"; }

RUN_DIR="$HERE/results/$(date +%Y-%m-%d_%H%M)_${LABEL}"
mkdir -p "$RUN_DIR"

prepare() {
    ssh "${SSH_OPTS[@]}" "$APP_SSH" 'cat > /tmp/perf-app.sh' < "$HERE/remote/app.sh"
    # The answer every captcha gets in the app's test mode.
    CAPTCHA_ANSWER=$(app check)
    tar -C "$HERE" -cf - k6 | load 'rm -rf ~/perf/k6 && mkdir -p ~/perf/out && tar -C ~/perf -xf -'

    local app_info local_sha local_dirty k6_version
    app_info=$(app info)
    local_sha=$(git -C "$HERE" rev-parse HEAD)
    local_dirty=$(git -C "$HERE" status --porcelain | wc -l | tr -d ' ')
    k6_version=$(load 'k6 version' | head -1)
    cat > "$RUN_DIR/meta.json" <<EOF
{
  "label": "$LABEL",
  "mode": "$MODE",
  "suite_version": $SUITE_VERSION,
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "local_git_sha": "$local_sha",
  "local_git_dirty_files": $local_dirty,
  "app": $app_info,
  "k6": "$k6_version",
  "seed_args": "$SEED_ARGS",
  "env_overrides": "$OVERRIDES"
}
EOF
}

stop_stats() { app stats-stop > /dev/null 2>&1 || true; }
trap stop_stats EXIT

# One k6 run into $1 at rate $2 with profile $3; $4 = 0 keeps the data of the previous run.
# Sets K6_CODE (0 = passed, 99 = SLO broken).
# Never called from a condition: bash would silently turn off `set -e` inside it.
run_once() {
    local dir=$1 rate=$2 profile=$3 reset=${4:-${RESET:-1}} code=0
    mkdir -p "$dir"
    echo "== $profile at $rate it/s -> ${dir#"$HERE"/}"
    if [ "$reset" = 1 ]; then
        # shellcheck disable=SC2086
        app reset $SEED_ARGS | tail -1
    fi
    app db-status > "$dir/mysql_before.tsv"
    app stats-start
    local started
    started=$(ssh "${SSH_OPTS[@]}" "$APP_SSH" 'date +%s')

    local vars="-e PROFILE=$profile -e RATE=$rate -e BASE_URL=$BASE_URL -e HOST_HEADER=$HOST_HEADER"
    vars+=" -e TOP=$TOP -e TOTAL=$TOTAL -e SLO_P95=${SLO_P95:-500} -e CAPTCHA_ANSWER=$CAPTCHA_ANSWER"
    vars+=" -e SUMMARY_OUT=../out/summary.json"
    for name in DURATION WARMUP WS_CLIENTS VIEWERS WS_PER_VU SEED USE_TOKEN; do
        [ -n "${!name:-}" ] && vars+=" -e $name=${!name}"
    done
    # vmstat on the load machine shows whether k6 itself ran out of CPU. Every socket is a
    # file, and the default limit of 1024 would stop k6 at about a thousand connections.
    load "ulimit -n \$(ulimit -Hn) && cd ~/perf/k6 && rm -f ../out/* && { TZ=UTC vmstat -n -t 2 > ../out/vmstat.txt & }; v=\$!;
        k6 run --quiet --no-color $vars main.js; c=\$?; kill \$v; exit \$c" || code=$?

    app stats-stop
    app db-status > "$dir/mysql_after.tsv"
    app foreign "$started" > "$dir/foreign.txt"
    app db-pool "$started" > "$dir/db_pool.txt"
    # A few hits from internet scanners are normal; an open tab makes hundreds.
    if [ "$(cut -d' ' -f1 "$dir/foreign.txt")" -gt 10 ]; then
        echo "WARNING: requests from outside the load machine during the run (count, ip=count):" >&2
        echo "  $(cat "$dir/foreign.txt"). Close open app tabs and repeat the run." >&2
    fi
    scp -q "${SSH_OPTS[@]}" "$APP_SSH:/tmp/perf/docker_stats.csv" "$APP_SSH:/tmp/perf/services.csv" \
        "$APP_SSH:/tmp/perf/vmstat.txt" "$dir/"
    scp -q "${SSH_OPTS[@]}" "$LOAD_SSH:perf/out/vmstat.txt" "$dir/loadgen_vmstat.txt" || true
    scp -q "${SSH_OPTS[@]}" "$LOAD_SSH:perf/out/summary.json" "$dir/" || {
        echo "k6 produced no summary (exit $code)" >&2
        exit 1
    }
    if [ "$code" != 0 ] && [ "$code" != 99 ]; then
        echo "k6 failed with exit code $code" >&2
        exit "$code"
    fi
    K6_CODE=$code
}

# Rates in order, STEP_DURATION each, until the SLO breaks. The data is reset before the first.
run_steps() {
    local dir=$1 reset=${RESET:-1}
    shift
    local DURATION=${DURATION:-$STEP_DURATION} WARMUP=${WARMUP:-$STEP_WARMUP} VIEWERS=${VIEWERS:-$STEP_VIEWERS}
    export DURATION WARMUP VIEWERS
    for rate in "$@"; do
        run_once "$dir/rate-$(printf %03d "$rate")" "$rate" load "$reset"
        reset=0
        if [ "$K6_CODE" != 0 ]; then
            echo "SLO broken at $rate it/s, stopping"
            break
        fi
    done
    unset DURATION WARMUP VIEWERS
}

prepare
case "$MODE" in
suite)
    # Two load runs show the spread between identical runs.
    DURATION=$LOAD_DURATION WARMUP=$LOAD_WARMUP VIEWERS=$LOAD_VIEWERS run_once "$RUN_DIR/load-a" "$LOAD_RATE" load
    DURATION=$LOAD_DURATION WARMUP=$LOAD_WARMUP VIEWERS=$LOAD_VIEWERS run_once "$RUN_DIR/load-b" "$LOAD_RATE" load
    DURATION=$UPLOAD_DURATION WARMUP=$UPLOAD_WARMUP run_once "$RUN_DIR/upload" "$UPLOAD_RATE" upload
    # shellcheck disable=SC2086
    run_steps "$RUN_DIR/step" $STEP_RATES
    ;;
smoke) run_once "$RUN_DIR" "${RATE:-2}" smoke ;;
load | soak) VIEWERS=${VIEWERS:-$LOAD_VIEWERS} run_once "$RUN_DIR" "${3:-${RATE:-$LOAD_RATE}}" "$MODE" ;;
upload) run_once "$RUN_DIR" "${3:-${RATE:-$UPLOAD_RATE}}" upload ;;
day) run_once "$RUN_DIR" "${3:-${RATE:-23}}" day ;;
step)
    set -- "${@:3}"
    # shellcheck disable=SC2086
    run_steps "$RUN_DIR" ${*:-$STEP_RATES}
    ;;
esac

for py in python3 python; do
    if "$py" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2> /dev/null; then
        exec "$py" "$HERE/compare.py" "$RUN_DIR"
    fi
done
echo "Python 3 not found; results are in $RUN_DIR" >&2
