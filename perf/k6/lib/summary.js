// Turns k6's end-of-test data into a compact summary.json plus a short text table.
// compare.py reads only summary.json, so its shape is the contract between the two.

function trend(metric) {
  if (!metric || !metric.values.count) return null;
  const v = metric.values;
  return {
    avg: v.avg,
    med: v.med,
    p90: v["p(90)"],
    p95: v["p(95)"],
    p99: v["p(99)"],
    max: v.max,
    count: v.count,
  };
}

function value(metric, key) {
  return metric ? metric.values[key] : null;
}

function rate(metric) {
  if (!metric) return { rate: null, count: 0 };
  return { rate: metric.values.rate, count: metric.values.passes + metric.values.fails };
}

export function summarize(data, meta) {
  const m = data.metrics;
  const ms = (name) => trend(m[name]);
  const endpoints = {};
  for (const name of ["list", "thread", "branch", "captcha", "create", "preview", "attach"]) {
    endpoints[name] = ms(`http_req_duration{name:${name},phase:main}`);
  }
  for (const name of ["reload", "reload-thread"]) {
    endpoints[name] = ms(`http_req_duration{name:${name},phase:live}`);
  }
  endpoints.media = ms("http_req_duration{phase:media}");
  const failed = {};
  for (const name of Object.keys(meta.mix)) {
    failed[name] = value(m[`http_req_failed{scenario:${name}}`], "rate");
  }
  const thresholds = {};
  for (const [name, metric] of Object.entries(m)) {
    if (metric.thresholds) {
      for (const [expr, result] of Object.entries(metric.thresholds)) {
        if (!/>=0$/.test(expr)) thresholds[`${name}: ${expr}`] = result.ok;
      }
    }
  }
  const { out, ...runMeta } = meta;
  const summary = {
    meta: { ...runMeta, finished_at: new Date().toISOString() },
    passed: Object.values(thresholds).every(Boolean),
    thresholds,
    http: {
      requests_main: value(m["http_reqs{phase:main}"], "count"),
      requests_live: value(m["http_reqs{phase:live}"], "count") || 0,
      failed_rate_live: value(m["http_req_failed{phase:live}"], "rate"),
      requests_total: value(m.http_reqs, "count"),
      failed_rate_main: value(m["http_req_failed{phase:main}"], "rate"),
      duration_main: ms("http_req_duration{phase:main}"),
      dropped_iterations: value(m.dropped_iterations, "count") || 0,
    },
    media: {
      requests: value(m["http_reqs{phase:media}"], "count") || 0,
      failed_rate: value(m["http_req_failed{phase:media}"], "rate"),
      bytes: value(m.media_bytes, "count") || 0,
    },
    endpoints,
    failed_by_scenario: failed,
    ws: {
      delivery: ms("ws_delivery_ms"),
      messages: value(m.ws_messages, "count") || 0,
      // New top-level comments heard by the connections that show the first page.
      front_events: value(m.ws_front_events, "count") || 0,
      errors: value(m.ws_errors, "count") || 0,
      sessions: value(m.ws_sessions, "count") || 0,
      connecting: ms("ws_connecting"),
    },
    // Rates with the number of samples: a rate of 1 out of 0 samples proves nothing.
    api_ok: rate(m.api_ok),
    content_ok: rate(m.content_ok),
    fresh: {
      list: rate(m.fresh_list),
      thread: rate(m.fresh_thread),
      visible: ms("visible_ms"),
    },
    attach_ready: { ...rate(m.attach_ready), time: ms("attach_ready_ms") },
    comments_created: value(m.comments_created, "count") || 0,
    comments_created_top: value(m.comments_created_top, "count") || 0,
    data_received_bytes: value(m.data_received, "count"),
    checks_rate: value(m.checks, "rate"),
  };
  return { [out]: JSON.stringify(summary, null, 2), stdout: text(summary) };
}

const fmt = (n) => (n == null ? "-" : n >= 100 ? n.toFixed(0) : n.toFixed(1));

function text(s) {
  const lines = [];
  const meta = s.meta;
  lines.push(`\n${meta.profile}: ${meta.rate} it/s for ${meta.duration} (+${meta.warmup} warmup), ${meta.ws_clients} ws clients`);
  lines.push(`result: ${s.passed ? "PASSED" : "FAILED"}`);
  for (const [name, ok] of Object.entries(s.thresholds)) lines.push(`  ${ok ? "ok  " : "FAIL"} ${name}`);
  const h = s.http;
  lines.push(
    `requests: ${h.requests_main} main, failed ${(100 * (h.failed_rate_main || 0)).toFixed(2)}%, dropped iterations ${h.dropped_iterations}`,
  );
  lines.push("\nlatency, ms        med     p95     p99     max   count");
  const row = (name, t) =>
    t &&
    lines.push(
      `${name.padEnd(14)}${fmt(t.med).padStart(8)}${fmt(t.p95).padStart(8)}${fmt(t.p99).padStart(8)}${fmt(t.max).padStart(8)}${String(t.count).padStart(8)}`,
    );
  row("all (main)", h.duration_main);
  for (const [name, t] of Object.entries(s.endpoints)) row(name, t);
  row("ws delivery", s.ws.delivery);
  row("visible", s.fresh.visible);
  row("attach ready", s.attach_ready.time);
  lines.push(`\nmedia: ${s.media.requests} files, ${(s.media.bytes / 2 ** 20).toFixed(1)} MiB`);
  lines.push(`ws: ${s.ws.messages} messages, ${s.ws.errors} errors; comments created: ${s.comments_created}\n`);
  return lines.join("\n");
}
