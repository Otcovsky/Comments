"""Reports and compares load test results from perf/results.

    python perf/compare.py --list
    python perf/compare.py RUN                 # one run, a step run or a whole suite
    python perf/compare.py BASELINE RUN [RUN]  # side by side, with the change against BASELINE
    python perf/compare.py --chain             # the runs of perf/chain.txt, each against the previous one
    python perf/compare.py --chain RUN RUN ... # the same for any runs
    python perf/compare.py ... --md            # Markdown table instead of plain text

RUN is a directory in perf/results, by full path or by any unique part of its name;
"name/load-b" picks one part of a suite, "name/rate-008" one step of a step run.
A step run is represented by its highest rate that still met the SLO. Suites are compared
part by part: load (mean of load-a and load-b), upload and capacity.
In a chain every "change" column is against the previous run, so each column shows what one
optimization gave; with three runs or more the last column ("total") is against the first.
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

RESULTS = Path(__file__).resolve().parent / "results"
CHAIN = Path(__file__).resolve().parent / "chain.txt"
# The k6 script keeps listeners connected this long after the main phase ends.
WS_TAIL = timedelta(seconds=5)
MYSQL_COUNTERS = [
    "Questions",
    "Innodb_rows_read",
    "Select_scan",
    "Created_tmp_disk_tables",
    "Slow_queries",
]


def find_run(name):
    path = Path(name)
    if path.is_dir():
        return path.resolve()
    # "part-of-run-name/rate-004" picks one step of a step run.
    head, _, step = name.partition("/")
    matches = [p for p in RESULTS.iterdir() if p.is_dir() and head in p.name]
    if len(matches) != 1:
        sys.exit(f"{head!r} matches {len(matches)} runs in {RESULTS}")
    if step and not (matches[0] / step).is_dir():
        sys.exit(f"No {step} in {matches[0].name}")
    return matches[0] / step if step else matches[0]


def seconds(value):
    number, unit = int(value[:-1]), value[-1]
    return number * {"s": 1, "m": 60, "h": 3600}[unit]


def main_window(summary):
    meta = summary["meta"]
    end = datetime.fromisoformat(meta["finished_at"].replace("Z", "+00:00")) - WS_TAIL
    return end - timedelta(seconds=seconds(meta["duration"])), end


def services(path):
    """Container name -> compose service, written by app.sh when sampling starts."""
    if not path.exists():
        return None
    with path.open(newline="") as f:
        return {name: service for name, service in csv.reader(f)}


def docker_stats(path, window, names=None):
    """Average and peak CPU % and peak memory (MiB) per compose service during the main
    phase; replicas of a service are summed. "all" sums every service of the app."""
    if not path.exists():
        return {}
    start, end = (t.timestamp() for t in window)
    # (timestamp, service) -> [cpu, mem]: one docker stats call covers every container.
    totals = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("cpu") or not start <= int(row["ts"]) <= end:
                continue
            if names is None:
                service = next((s for s in ("backend", "db", "frontend") if s in row["name"]), row["name"])
            elif row["name"] in names:
                service = names[row["name"]]
            else:
                continue  # not part of the app
            used = row["mem"].split("/")[0].strip()
            for key in (service, "all"):
                total = totals.setdefault((row["ts"], key), [0.0, 0.0])
                total[0] += float(row["cpu"].rstrip("%"))
                total[1] += mebibytes(used)
    samples = {}
    for (_, service), value in totals.items():
        samples.setdefault(service, []).append(value)
    return {
        service: {
            "cpu_avg": mean(c for c, _ in values),
            "cpu_max": max(c for c, _ in values),
            "mem_max": max(m for _, m in values),
        }
        for service, values in samples.items()
    }


def mebibytes(text):
    match = re.match(r"([\d.]+)\s*([KMG]i?B|B)", text)
    if not match:
        return 0.0
    number, unit = float(match[1]), match[2]
    return number * {"B": 1 / 2**20, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1024}.get(unit, 1)


def vmstat(path, window):
    """Host CPU split (us/sy/id/wa, %) and disk traffic (bi/bo, KiB/s) during the main phase."""
    if not path.exists():
        return {}
    columns, rows = None, []
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts or parts[0] == "procs":
            continue
        if parts[0] == "r":
            columns = parts
            continue
        if columns is None:
            continue
        stamp = datetime.fromisoformat(f"{parts[-2]}T{parts[-1]}").replace(tzinfo=timezone.utc)
        if window[0] <= stamp <= window[1]:
            rows.append(dict(zip(columns, parts)))
    if not rows:
        return {}
    return {key: mean(int(r[key]) for r in rows) for key in ("us", "sy", "id", "wa", "bi", "bo")}


def mysql_deltas(run):
    def load(name):
        path = run / name
        if not path.exists():
            return {}
        pairs = (line.split("\t") for line in path.read_text().splitlines() if "\t" in line)
        return {k: v for k, v in pairs}

    before, after = load("mysql_before.tsv"), load("mysql_after.tsv")
    return {
        key: int(after[key]) - int(before[key])
        for key in MYSQL_COUNTERS
        if key in before and key in after
    }


def foreign(run):
    """Requests from outside the load machine during the run (written by run.sh)."""
    path = run / "foreign.txt"
    return int(path.read_text().split()[0]) if path.exists() else None


def load_run(run):
    summary = json.loads((run / "summary.json").read_text())
    window = main_window(summary)
    return {
        "foreign": foreign(run),
        "dir": run,
        "summary": summary,
        "docker": docker_stats(run / "docker_stats.csv", window, services(run / "services.csv")),
        "host": vmstat(run / "vmstat.txt", window),
        "loadgen": vmstat(run / "loadgen_vmstat.txt", window),
        "mysql": mysql_deltas(run),
    }


def steps(run):
    return sorted(p for p in run.iterdir() if p.is_dir() and p.name.startswith("rate-"))


def is_suite(run):
    return (run / "load-a").is_dir()


def representative(run):
    """Runs that stand for `run` in a comparison, and the column name: a plain run, or the
    best passing step of a step run (the last one if none passed)."""
    if (run / "summary.json").exists():
        return [load_run(run)], run.name
    loaded = [load_run(step) for step in steps(run)]
    if not loaded:
        sys.exit(f"No summary.json in {run}")
    passing = [r for r in loaded if r["summary"]["passed"]]
    best = (passing or loaded)[-1]
    return [best], f"{run.name} @{best['summary']['meta']['rate']}"


def get(data, *path):
    for key in path:
        if data is None:
            return None
        data = data.get(key)
    return data


def per_request(r, counter):
    """Per API request: attachment downloads are served by nginx and never reach MySQL."""
    value = r["mysql"].get(counter)
    requests = get(r, "summary", "http", "requests_total")
    if requests is not None:
        requests -= get(r, "summary", "media", "requests") or 0
    return value / requests if value is not None and requests else None


def rate_of(r, *path):
    """A k6 Rate as %, or None without samples."""
    value = get(r, "summary", *path)
    return 100 * value["rate"] if value and value.get("count") else None


def delivery_ratio(r):
    s = r["summary"]
    if "front_events" in s["ws"]:
        # Suite 2: the server sends a new top-level comment only to the connections that
        # show the first page of the default ordering.
        expected = s["comments_created_top"] * s["meta"]["ws_front"]
        return s["ws"]["front_events"] / expected if expected else None
    # Suite 1 and earlier: every listener got every event.
    expected = s["comments_created"] * s["meta"]["ws_clients"]
    return s["ws"]["messages"] / expected if expected else None


def percent(rate):
    return 100 * rate if rate is not None else None


def mib(size):
    return size / 2**20 if size is not None else None


# (title, getter, lower_is_better, format)
ROWS = [
    ("SLO passed", lambda r: r["summary"]["passed"], None, "bool"),
    ("foreign requests", lambda r: r["foreign"], True, "int"),
    ("rate, it/s", lambda r: get(r, "summary", "meta", "rate"), None, "num"),
    ("requests (main)", lambda r: get(r, "summary", "http", "requests_main"), None, "int"),
    ("tab reloads (live)", lambda r: get(r, "summary", "http", "requests_live"), None, "int"),
    ("failed, %", lambda r: 100 * (get(r, "summary", "http", "failed_rate_main") or 0), True, "num"),
    ("dropped iterations", lambda r: get(r, "summary", "http", "dropped_iterations"), True, "int"),
    ("all p50, ms", lambda r: get(r, "summary", "http", "duration_main", "med"), True, "num"),
    ("all p95, ms", lambda r: get(r, "summary", "http", "duration_main", "p95"), True, "num"),
    ("all p99, ms", lambda r: get(r, "summary", "http", "duration_main", "p99"), True, "num"),
    *[
        (f"{name} p95, ms", lambda r, n=name: get(r, "summary", "endpoints", n, "p95"), True, "num")
        for name in ("list", "thread", "branch", "captcha", "create", "preview", "attach", "reload", "reload-thread", "media")
    ],
    ("api ok, %", lambda r: rate_of(r, "api_ok"), False, "num"),
    ("content ok, %", lambda r: rate_of(r, "content_ok"), False, "num"),
    ("fresh list, %", lambda r: rate_of(r, "fresh", "list"), False, "num"),
    ("fresh thread, %", lambda r: rate_of(r, "fresh", "thread"), False, "num"),
    ("visible p50, ms", lambda r: get(r, "summary", "fresh", "visible", "med"), True, "num"),
    ("visible p95, ms", lambda r: get(r, "summary", "fresh", "visible", "p95"), True, "num"),
    ("attachment ready, %", lambda r: rate_of(r, "attach_ready"), False, "num"),
    ("attachment ready p95, ms", lambda r: get(r, "summary", "attach_ready", "time", "p95"), True, "num"),
    ("media files", lambda r: get(r, "summary", "media", "requests"), None, "int"),
    ("media failed, %", lambda r: percent(get(r, "summary", "media", "failed_rate")), True, "num"),
    ("media MiB", lambda r: mib(get(r, "summary", "media", "bytes")), None, "num"),
    ("ws delivery p50, ms", lambda r: get(r, "summary", "ws", "delivery", "med"), True, "num"),
    ("ws delivery p95, ms", lambda r: get(r, "summary", "ws", "delivery", "p95"), True, "num"),
    ("ws delivered, %", lambda r: 100 * delivery_ratio(r) if delivery_ratio(r) is not None else None, False, "num"),
    ("app CPU avg, % (all services)", lambda r: get(r, "docker", "all", "cpu_avg"), True, "num"),
    ("backend CPU avg, %", lambda r: get(r, "docker", "backend", "cpu_avg"), True, "num"),
    ("backend CPU max, %", lambda r: get(r, "docker", "backend", "cpu_max"), True, "num"),
    ("db CPU avg, %", lambda r: get(r, "docker", "db", "cpu_avg"), True, "num"),
    ("nginx CPU avg, %", lambda r: get(r, "docker", "frontend", "cpu_avg"), True, "num"),
    ("worker CPU avg, %", lambda r: get(r, "docker", "worker", "cpu_avg"), True, "num"),
    ("broker CPU avg, %", lambda r: get(r, "docker", "broker", "cpu_avg"), True, "num"),
    ("beat CPU avg, %", lambda r: get(r, "docker", "beat", "cpu_avg"), True, "num"),
    ("backend mem max, MiB", lambda r: get(r, "docker", "backend", "mem_max"), True, "num"),
    ("worker mem max, MiB", lambda r: get(r, "docker", "worker", "mem_max"), True, "num"),
    ("broker mem max, MiB", lambda r: get(r, "docker", "broker", "mem_max"), True, "num"),
    ("beat mem max, MiB", lambda r: get(r, "docker", "beat", "mem_max"), True, "num"),
    ("db mem max, MiB", lambda r: get(r, "docker", "db", "mem_max"), True, "num"),
    ("host CPU idle, %", lambda r: get(r, "host", "id"), False, "num"),
    ("host iowait, %", lambda r: get(r, "host", "wa"), True, "num"),
    ("host disk read, KiB/s", lambda r: get(r, "host", "bi"), True, "num"),
    ("host disk write, KiB/s", lambda r: get(r, "host", "bo"), True, "num"),
    ("load machine CPU idle, %", lambda r: get(r, "loadgen", "id"), None, "num"),
    ("SQL queries / request", lambda r: per_request(r, "Questions"), True, "num"),
    ("rows read / request", lambda r: per_request(r, "Innodb_rows_read"), True, "num"),
    ("full scans / request", lambda r: per_request(r, "Select_scan"), True, "num"),
    ("tmp disk tables", lambda r: r["mysql"].get("Created_tmp_disk_tables"), True, "int"),
    ("slow queries", lambda r: r["mysql"].get("Slow_queries"), True, "int"),
]


def fmt(value, kind):
    if value is None:
        return "-"
    if kind == "bool":
        return "yes" if value else "NO"
    if kind == "int":
        return str(int(value))
    return f"{value:.0f}" if abs(value) >= 100 else f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"


def change(base, value, lower_is_better):
    if lower_is_better is None or base is None or value is None:
        return ""
    if base == 0:
        return "" if value == 0 else ("worse" if (value > 0) == lower_is_better else "better")
    delta = (value - base) / abs(base) * 100
    if abs(delta) < 5:
        return f"{delta:+.0f}%"
    better = (delta < 0) == lower_is_better
    return f"{delta:+.0f}% {'better' if better else 'worse'}"


def render(header, rows, markdown):
    if markdown:
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(row) + " |" for row in rows]
        return "\n".join(lines)
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    line = lambda cells: "  ".join(str(c).ljust(w) if i == 0 else str(c).rjust(w) for i, (c, w) in enumerate(zip(cells, widths)))
    return "\n".join([line(header), line(["-" * w for w in widths]), *map(line, rows)])


def combined(getter, group, kind):
    """One value for a group of identical runs: the mean (for SLO: passed in all)."""
    values = [v for v in (getter(r) for r in group) if v is not None]
    if not values:
        return None
    if kind == "bool":
        return all(values)
    return mean(values)


def version_warning(loaded):
    versions = [{get(r, "summary", "meta", "suite_version") or 0 for r in group} for group, _ in loaded]
    if len(set().union(*versions)) > 1:
        names = ", ".join(f"{name}: v{'/'.join(map(str, sorted(v)))}" for (_, name), v in zip(loaded, versions))
        return f"WARNING: different suite versions ({names}), the numbers are not comparable.\n\n"
    return ""


def compare(runs, markdown, chain=False):
    return compare_loaded([representative(run) for run in runs], markdown, chain)


def compare_loaded(loaded, markdown, chain=False):
    total = chain and len(loaded) > 2
    header = ["metric"]
    for index, (_, name) in enumerate(loaded):
        header.append(name)
        if index:
            header.append("vs prev" if chain else "change")
    if total:
        header.append("total")
    rows = []
    for title, getter, lower, kind in ROWS:
        values = [combined(getter, group, kind) for group, _ in loaded]
        if all(v is None for v in values):
            continue
        row = [title, fmt(values[0], kind)]
        for index, value in enumerate(values[1:], 1):
            row += [fmt(value, kind), change(values[index - 1 if chain else 0], value, lower)]
        if total:
            row.append(change(values[0], values[-1], lower))
        rows.append(row)
    return version_warning(loaded) + render(header, rows, markdown)


def compare_suites(runs, markdown, chain=False):
    """Part by part: the mean of both load runs, the upload run and the capacity."""
    load = [([load_run(run / "load-a"), load_run(run / "load-b")], run.name) for run in runs]
    parts = [("load (mean of load-a and load-b)", compare_loaded(load, markdown, chain))]
    upload = [([load_run(run / "upload")], run.name) for run in runs if (run / "upload" / "summary.json").exists()]
    if upload:
        parts.append(("upload", compare_loaded(upload, markdown, chain)))
    stepped = [run / "step" for run in runs if (run / "step").is_dir() and steps(run / "step")]
    if stepped:
        lines, previous = [], None
        for run in stepped:
            rate = capacity(run)
            line = f"{run.parent.name}: {verdict(rate)}"
            if chain and previous is not None:
                line += f" ({change(previous, rate, False) or 'n/a'} vs prev)"
            lines.append(line)
            previous = rate
        best = compare_loaded([representative(run) for run in stepped], markdown, chain)
        parts.append(("capacity", "\n".join(lines) + "\n\nbest passing step:\n" + best))
    return "\n\n".join(f"## {title}\n\n{body}" for title, body in parts)


def read_chain():
    """Run names and descriptions from perf/chain.txt, in order."""
    if not CHAIN.exists():
        sys.exit(f"No {CHAIN}")
    entries = []
    for line in CHAIN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, _, description = line.partition(" ")
            entries.append((name, description.strip()))
    return entries


def capacity(run):
    """The highest rate of a step run that met the SLO."""
    rate = None
    for step in steps(run):
        summary = json.loads((step / "summary.json").read_text())
        if summary["passed"]:
            rate = summary["meta"]["rate"]
    return rate


def verdict(rate):
    return f"capacity: {rate} it/s within the SLO" if rate else "no rate met the SLO"


def step_report(run, markdown):
    header = ["rate", "SLO", "req/s", "p50", "p95", "p99", "failed %", "dropped", "app CPU", "db CPU", "fresh %", "ws p95", "k6 idle"]
    rows, capacity = [], None
    for step in steps(run):
        r = load_run(step)
        s = r["summary"]
        duration = seconds(s["meta"]["duration"])
        if s["passed"]:
            capacity = s["meta"]["rate"]
        rows.append(
            [
                str(s["meta"]["rate"]),
                "ok" if s["passed"] else "FAIL",
                fmt(s["http"]["requests_main"] / duration, "num"),
                fmt(get(s, "http", "duration_main", "med"), "num"),
                fmt(get(s, "http", "duration_main", "p95"), "num"),
                fmt(get(s, "http", "duration_main", "p99"), "num"),
                fmt(100 * (s["http"]["failed_rate_main"] or 0), "num"),
                str(s["http"]["dropped_iterations"]),
                fmt(get(r, "docker", "all", "cpu_avg"), "num"),
                fmt(get(r, "docker", "db", "cpu_avg"), "num"),
                fmt(rate_of(r, "fresh", "list"), "num"),
                fmt(get(s, "ws", "delivery", "p95"), "num"),
                fmt(get(r, "loadgen", "id"), "num"),
            ]
        )
    table = render(header, rows, markdown)
    return f"{run.name}\n{table}\n\n{verdict(capacity)}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--list", action="store_true", help="list runs")
    parser.add_argument("--md", action="store_true", help="Markdown output")
    parser.add_argument("--chain", action="store_true", help="each run against the previous one")
    args = parser.parse_args()

    if args.chain and not args.runs:
        entries = read_chain()
        if args.md:
            print("| # | run | change |\n|---|---|---|")
            print("\n".join(f"| {i} | {name} | {text} |" for i, (name, text) in enumerate(entries)))
        else:
            print("\n".join(f"{i}. {name}  {text}" for i, (name, text) in enumerate(entries)))
        print()
        args.runs = [name for name, _ in entries]
    if args.list or not args.runs:
        for run in sorted(p for p in RESULTS.iterdir() if p.is_dir()):
            kind = "suite" if is_suite(run) else "single" if (run / "summary.json").exists() else f"step x{len(steps(run))}"
            print(f"{run.name}  ({kind})")
        return
    runs = [find_run(name) for name in args.runs]
    suites = [is_suite(run) for run in runs]
    if any(suites) and not all(suites):
        sys.exit("A suite is compared only with another suite; pick a part instead, e.g. NAME/load-a.")
    if all(suites):
        print(compare_suites(runs, args.md, args.chain))
        if len(runs) == 1 and (runs[0] / "step").is_dir():
            print("\n## steps\n\n" + step_report(runs[0] / "step", args.md))
    elif len(runs) == 1 and not (runs[0] / "summary.json").exists():
        print(step_report(runs[0], args.md))
    else:
        print(compare(runs, args.md, args.chain))


if __name__ == "__main__":
    main()
