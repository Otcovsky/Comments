// Load test of the comments app. All knobs come from env variables (see perf/README.md).
//
// The HTTP mix runs at a fixed arrival rate (RATE iterations per second in total), so a
// slower build gets exactly the same traffic as a faster one and the difference shows up
// in latency, errors and dropped iterations instead of in a lower request count.
//
// SUITE_VERSION names the user behaviour modelled here. Anything that changes it (the mix,
// which threads people open, how open tabs react to events) is a new suite version, and
// runs of different versions are not compared.
import http from "k6/http";
import exec from "k6/execution";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { WebSocket } from "k6/websockets";
import { setTimeout } from "k6/timers";

import { summarize } from "./lib/summary.js";

// 2: GraphQL API, threads unfolded level by level, a WebSocket viewport per tab.
export const SUITE_VERSION = 2;

const env = (name, fallback) => __ENV[name] || fallback;

const PROFILES = {
  smoke: { rate: 2, duration: "30s", warmup: "5s", wsClients: 10, viewers: 10 },
  load: { rate: 4, duration: "5m", warmup: "30s", wsClients: 50, viewers: 10 },
  soak: { rate: 4, duration: "30m", warmup: "30s", wsClients: 50, viewers: 10 },
  // Only uploads, so their latency gets enough samples to compare.
  upload: { rate: 2, duration: "3m", warmup: "15s", wsClients: 0, viewers: 0 },
};
const PROFILE = env("PROFILE", "smoke");
const defaults = PROFILES[PROFILE];
if (!defaults) throw new Error(`Unknown PROFILE ${PROFILE}`);

const BASE_URL = env("BASE_URL", "http://localhost");
// The WebSocket Origin must be one of DJANGO_ALLOWED_HOSTS, so a private address needs the public Host.
const HOST = env("HOST_HEADER", "");
const ORIGIN = `http://${HOST || BASE_URL.replace(/^https?:\/\//, "")}`;
const RATE = Number(env("RATE", defaults.rate));
const DURATION = env("DURATION", defaults.duration);
const WARMUP = env("WARMUP", defaults.warmup);
const WS_CLIENTS = Number(env("WS_CLIENTS", defaults.wsClients));
// Listeners that also behave like an open browser tab: they reload what they show on
// events, as the frontend does. The rest only report their viewport and count events.
const VIEWERS = Math.min(WS_CLIENTS, Number(env("VIEWERS", defaults.viewers)));
const SLO_P95 = Number(env("SLO_P95", 500));
// Must match `manage.py seed_perf`: ids 1..TOTAL, the first TOP are top-level.
const TOP = Number(env("TOP", 10000));
const TOTAL = Number(env("TOTAL", 60000));
const CAPTCHA_ANSWER = env("CAPTCHA_ANSWER", "PERF1");
const SEED = Number(env("SEED", 1));
// Off in the suite: a VU solves one captcha and posts the rest with the token it got for it.
const USE_TOKEN = env("USE_TOKEN", "") === "1";

// The frontend's default page size and ordering (App.jsx).
const PAGE_SIZE = 25;
const DEFAULT_ORDERING = "CREATED_AT_DESC";
// Share of RATE per scenario. A write is two requests: captcha + post.
const MIX =
  PROFILE === "upload"
    ? { attach: 1 }
    : { list: 0.6, thread: 0.25, write: 0.1, preview: 0.03, attach: 0.02 };
// Like the browser: a thread shows its images right away, a text file opens on click.
const TEXT_OPEN_SHARE = 0.2;
// A reader of a thread unfolds a reply that has replies of its own with this chance, and
// then maybe one level deeper, up to this many levels.
const UNFOLD_SHARE = 0.5;
const MAX_UNFOLD_LEVELS = 3;
// A posted attachment must be downloadable within this time (a queue may process it later).
const ATTACH_READY_TIMEOUT = 10;
// App.jsx: a tab reloads its page after a random delay up to this, and the comments that
// arrive meanwhile share that one reload.
const LIVE_RELOAD_MAX_DELAY_MS = 1000;
// What each tab shows, by its index (repeats after ten): page, ordering and the row whose
// thread is open (null: none). Half of them sit on the first page, as most visitors do.
const VIEWS = [
  [1, DEFAULT_ORDERING, 0],
  [1, DEFAULT_ORDERING, 1],
  [1, DEFAULT_ORDERING, 2],
  [1, DEFAULT_ORDERING, null],
  [1, DEFAULT_ORDERING, null],
  [2, DEFAULT_ORDERING, 0],
  [1, "USERNAME_ASC", 3],
  [3, DEFAULT_ORDERING, null],
  [1, "EMAIL_DESC", null],
  [120, "CREATED_AT_ASC", 4],
];
const view = (index) => VIEWS[index % VIEWS.length];
// A new top-level comment is the first row of this page, so the server tells every
// connection showing it: these connections measure how many events get through.
const showsFront = (index) => view(index)[0] === 1 && view(index)[1] === DEFAULT_ORDERING;
let frontConnections = 0;
for (let index = 0; index < WS_CLIENTS; index++) if (showsFront(index)) frontConnections++;

const headers = HOST ? { Host: HOST } : {};
const fixtures = {
  jpg: open("./fixtures/photo.jpg", "b"),
  gif: open("./fixtures/anim.gif", "b"),
  txt: open("./fixtures/note.txt"),
};

const wsDelivery = new Trend("ws_delivery_ms", true);
const wsMessages = new Counter("ws_messages");
const wsFrontEvents = new Counter("ws_front_events");
const wsErrors = new Counter("ws_errors");
const commentsCreated = new Counter("comments_created");
const topCreated = new Counter("comments_created_top");
const mediaBytes = new Counter("media_bytes");
// GraphQL reports errors with status 200, so http_req_failed does not see them: a refused
// write would look like a fast one. Every operation must return data and no errors.
const apiOk = new Rate("api_ok");
// Responses that have data but wrong content (a broken cache, a lost write).
const contentOk = new Rate("content_ok");
// A tab that reloads after an event must see the new comment (a stale cache fails this).
const freshList = new Rate("fresh_list");
const freshThread = new Rate("fresh_thread");
// From the moment of the POST until a reloaded tab shows the comment.
const visible = new Trend("visible_ms", true);
// A posted attachment can be downloaded, and how long after the POST response.
const attachReady = new Rate("attach_ready");
const attachReadyTime = new Trend("attach_ready_ms", true);

function seconds(value) {
  const match = /^(\d+)(s|m|h)$/.exec(value);
  if (!match) throw new Error(`Duration must look like 30s, 5m or 1h: ${value}`);
  return Number(match[1]) * { s: 1, m: 60, h: 3600 }[match[2]];
}

function arrival(fn, share) {
  // Per minute, so small shares still get at least one iteration.
  const perMinute = Math.max(1, Math.round(RATE * share * 60));
  const vus = Math.ceil((perMinute / 60) * 2) + 2;
  return {
    executor: "constant-arrival-rate",
    exec: fn,
    rate: perMinute,
    timeUnit: "1m",
    duration: DURATION,
    startTime: WARMUP,
    preAllocatedVUs: vus,
    maxVUs: Math.max(vus * 10, 50),
    tags: { phase: "main" },
  };
}

const scenarios = {
  warmup: {
    executor: "constant-arrival-rate",
    exec: "warmup",
    rate: Math.max(1, Math.round(RATE / 2)),
    timeUnit: "1s",
    duration: WARMUP,
    preAllocatedVUs: 5,
    maxVUs: 50,
    tags: { phase: "warmup" },
  },
};
for (const [name, share] of Object.entries(MIX)) scenarios[name] = arrival(name, share);
if (WS_CLIENTS > 0) {
  scenarios.ws = {
    executor: "per-vu-iterations",
    exec: "listen",
    vus: WS_CLIENTS,
    iterations: 1,
    maxDuration: `${seconds(WARMUP) + seconds(DURATION) + 30}s`,
    tags: { phase: "ws" },
  };
}

// What people do carries phase:main and its p95 decides the SLO. Reloads of open tabs on
// events carry phase:live: they come in the background, so their latency is reported as
// `reload` and `visible` but does not decide the capacity; their errors and stale results
// still fail the run. Attachment downloads carry phase:media.
const thresholds = {
  "http_req_failed{phase:main}": ["rate<0.01"],
  "http_req_duration{phase:main}": [`p(95)<${SLO_P95}`],
  "http_reqs{phase:main}": ["count>=0"],
  "http_req_failed{phase:live}": ["rate<0.01"],
  "http_reqs{phase:live}": ["count>=0"],
  "http_req_failed{phase:media}": ["rate<0.01"],
  "http_reqs{phase:media}": ["count>=0"],
  dropped_iterations: ["count<1"],
  api_ok: ["rate>0.99"],
  content_ok: ["rate>0.99"],
};
if (VIEWERS > 0) thresholds.fresh_list = ["rate>0.99"];
if (MIX.attach) thresholds.attach_ready = ["rate>0.99"];
// Listing them here makes k6 keep per-scenario numbers in the summary.
for (const name of Object.keys(MIX)) {
  thresholds[`http_req_duration{scenario:${name}}`] = ["max>=0"];
  thresholds[`http_req_failed{scenario:${name}}`] = ["rate>=0"];
}
// Per operation, main phase only (warmup requests have the same names).
for (const name of ["list", "thread", "branch", "captcha", "create", "preview", "attach"]) {
  thresholds[`http_req_duration{name:${name},phase:main}`] = ["max>=0"];
}
for (const name of ["reload", "reload-thread"]) {
  thresholds[`http_req_duration{name:${name},phase:live}`] = ["max>=0"];
}
thresholds["http_req_duration{phase:media}"] = ["max>=0"];

export const options = {
  scenarios,
  thresholds,
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "count"],
  discardResponseBodies: false,
  setupTimeout: "60s",
};

// Math.random differs between runs; a seeded generator per VU gives every run the same
// sequence of choices in each VU, so only the scheduling between VUs varies.
let state = null;
function rand() {
  if (state === null) state = (SEED * 1000003 + exec.vu.idInTest * 7919) >>> 0;
  // mulberry32
  state = (state + 0x6d2b79f5) >>> 0;
  let t = state;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

function pick(weights) {
  let roll = rand() * weights.reduce((sum, [, w]) => sum + w, 0);
  for (const [value, weight] of weights) {
    roll -= weight;
    if (roll < 0) return value;
  }
  return weights[weights.length - 1][0];
}

const randInt = (min, max) => min + Math.floor(rand() * (max - min + 1));
const choice = (items) => items[Math.floor(rand() * items.length)];

function author() {
  // A stable author per VU: the first post creates it, later ones reuse it.
  const id = exec.vu.idInTest;
  return { username: `perf${id}`, email: `perf${id}@load.test` };
}

function marker() {
  // Tabs measure delivery from this timestamp (same machine, same clock).
  return `perf ts${Date.now()}`;
}

function sentAt(text) {
  const match = /ts(\d{13})/.exec(text);
  return match ? Number(match[1]) : null;
}

// The operations of frontend/src/api.js, with the same fields.
const GRAPHQL_URL = `${BASE_URL}/graphql/`;
const COMMENT_FIELDS = "id parentId rootId username email homePage text attachment attachmentType createdAt replyCount";
const LIST_QUERY = `query Comments($page: Int!, $pageSize: Int!, $ordering: CommentOrdering!) {
  comments(page: $page, pageSize: $pageSize, ordering: $ordering) { count results { ${COMMENT_FIELDS} } }
}`;
const REPLIES_QUERY = `query Replies($parentId: ID!) { replies(parentId: $parentId) { ${COMMENT_FIELDS} } }`;
const CREATE_MUTATION = `mutation CreateComment($input: CommentInput!, $attachment: Upload) {
  createComment(input: $input, attachment: $attachment) { comment { ${COMMENT_FIELDS} } token tokenExpiresIn }
}`;
const PREVIEW_QUERY = "query Preview($text: String!) { previewText(text: $text) }";
const CAPTCHA_MUTATION = "mutation Captcha { issueCaptcha { key image } }";

const params = (name, phase, extra) => ({
  headers: { ...headers, "Content-Type": "application/json", ...extra },
  tags: phase ? { name, phase } : { name },
});
const body = (query, variables) => JSON.stringify({ query, variables });

function json(res) {
  try {
    return res.json();
  } catch (error) {
    return undefined;
  }
}

// The data of a GraphQL response, or null for an HTTP failure or any GraphQL error.
function dataOf(res, name) {
  const payload = res.status === 200 ? json(res) : undefined;
  const ok = Boolean(payload && payload.data && !payload.errors);
  check(res, { [`${name} ok`]: () => ok });
  apiOk.add(ok);
  return ok ? payload.data : null;
}

const graphql = (name, query, variables, phase) =>
  dataOf(http.post(GRAPHQL_URL, body(query, variables), params(name, phase)), name);

const listVariables = (page, ordering) => ({ page, pageSize: PAGE_SIZE, ordering });

function listOk(comments) {
  return Array.isArray(comments.results) && comments.results.length === PAGE_SIZE && comments.count >= TOP;
}

function repliesOk(replies, parentId) {
  return Array.isArray(replies) && replies.every((c) => c.parentId === parentId);
}

// The roots people see in the list, read once before the test: they open these threads and
// reply in them, as on a real site.
export function setup() {
  const pages = [];
  for (let page = 1; page <= 5; page++) {
    const data = graphql("setup", LIST_QUERY, listVariables(page, DEFAULT_ORDERING), "setup");
    if (!data) throw new Error(`setup: list page ${page} failed`);
    pages.push(data.comments.results.map((c) => c.id));
  }
  return { front: pages[0], next: pages.slice(1).flat() };
}

// A thread is opened from the list; a few readers go further than the first pages.
function someRoot(data) {
  return pick([
    [choice(data.front), 70],
    [choice(data.next), 20],
    [String(randInt(1, TOP)), 10],
  ]);
}

// A reply goes to a comment on screen: mostly a root from the list, sometimes a reply deep
// in a thread.
function someParent(data) {
  return pick([
    [choice(data.front), 70],
    [choice(data.next), 20],
    [String(randInt(1, TOTAL)), 10],
  ]);
}

export function list() {
  const pages = Math.ceil(TOP / PAGE_SIZE);
  const page = pick([
    [1, 70],
    [randInt(2, 5), 20],
    [randInt(6, pages), 10],
  ]);
  const ordering = pick([
    [DEFAULT_ORDERING, 70],
    ["CREATED_AT_ASC", 6],
    ["USERNAME_ASC", 6],
    ["USERNAME_DESC", 6],
    ["EMAIL_ASC", 6],
    ["EMAIL_DESC", 6],
  ]);
  const data = graphql("list", LIST_QUERY, listVariables(page, ordering));
  if (data) contentOk.add(listOk(data.comments));
}

function replies(parentId, name, phase) {
  const data = graphql(name, REPLIES_QUERY, { parentId }, phase);
  if (!data) return null;
  contentOk.add(repliesOk(data.replies, parentId));
  return data.replies;
}

// Like Thread.jsx: an open thread shows the direct replies of its root, and every unfolded
// reply shows its own direct replies, one request per level.
export function thread(data) {
  let shown = replies(someRoot(data), "thread");
  if (!shown) return;
  media(shown);
  for (let level = 1; level <= MAX_UNFOLD_LEVELS && rand() < UNFOLD_SHARE; level++) {
    const unfoldable = shown.filter((c) => c.replyCount > 0);
    if (!unfoldable.length) return;
    shown = replies(choice(unfoldable).id, "branch");
    if (!shown) return;
    media(shown);
  }
}

function media(comments) {
  const phase = exec.scenario.name === "warmup" ? "warmup" : "media";
  const mediaParams = { headers, responseType: "none", tags: { name: "media", phase } };
  const requests = comments
    .filter((c) => c.attachment)
    .filter((c) => c.attachmentType === "IMAGE" || rand() < TEXT_OPEN_SHARE)
    .map((c) => ["GET", `${BASE_URL}${c.attachment}`, null, mediaParams]);
  if (!requests.length) return;
  // http.batch fetches up to 6 at a time per host, as a browser does.
  for (const r of http.batch(requests)) {
    check(r, { "media 200": (x) => x.status === 200 });
    mediaBytes.add(Number(r.headers["Content-Length"] || 0));
  }
}

export function warmup(data) {
  rand() < 0.7 ? list() : thread(data);
}

function captcha() {
  const data = graphql("captcha", CAPTCHA_MUTATION, {});
  return data ? data.issueCaptcha.key : null;
}

let token = null;

// `text` gets the delivery marker right before the POST, so the delivery time excludes the captcha.
function post(data, text, file, name) {
  const input = { ...author(), homePage: "", text: "", parentId: null, captchaKey: null, captchaValue: null };
  const auth = {};
  if (token) {
    auth.Authorization = `Bearer ${token}`;
  } else {
    input.captchaKey = captcha();
    if (!input.captchaKey) return null;
    input.captchaValue = CAPTCHA_ANSWER;
  }
  if (rand() < 0.5) input.parentId = someParent(data);
  input.text = `${marker()} ${text}`;
  const variables = { input, attachment: null };
  let res;
  if (file) {
    // The GraphQL multipart request, as in frontend/src/api.js.
    const multipart = {
      operations: body(CREATE_MUTATION, variables),
      map: JSON.stringify({ 0: ["variables.attachment"] }),
      0: file,
    };
    res = http.post(GRAPHQL_URL, multipart, { headers: { ...headers, ...auth }, tags: { name } });
  } else {
    res = http.post(GRAPHQL_URL, body(CREATE_MUTATION, variables), params(name, null, auth));
  }
  const result = dataOf(res, name);
  if (!result) return null;
  const created = result.createComment;
  if (USE_TOKEN && !token) token = created.token;
  commentsCreated.add(1);
  if (input.parentId === null) topCreated.add(1);
  contentOk.add(typeof created.comment.id === "string" && created.comment.id !== "");
  return created.comment;
}

export function write(data) {
  post(data, "<i>load</i> test comment", null, "create");
}

export function attach(data) {
  const file = pick([
    [http.file(fixtures.jpg, "photo.jpg", "image/jpeg"), 60],
    [http.file(fixtures.gif, "anim.gif", "image/gif"), 25],
    [http.file(fixtures.txt, "note.txt", "text/plain"), 15],
  ]);
  const comment = post(data, "with attachment", file, "attach");
  if (!comment) return;
  contentOk.add(typeof comment.attachment === "string");
  if (typeof comment.attachment === "string") waitForFile(comment.attachment);
}

// The author sees the posted file right away; poll in case it is processed in the background.
// Until then the file is not there, and that 404 is expected, not a failed request.
function waitForFile(path) {
  const started = Date.now();
  const fileParams = {
    headers,
    responseType: "none",
    responseCallback: http.expectedStatuses(200, 404),
    tags: { name: "attach-file", phase: "media" },
  };
  for (;;) {
    const res = http.get(`${BASE_URL}${path}`, fileParams);
    if (res.status === 200) {
      attachReady.add(true);
      attachReadyTime.add(Date.now() - started);
      return;
    }
    if (Date.now() - started > ATTACH_READY_TIMEOUT * 1000) {
      attachReady.add(false);
      return;
    }
    sleep(0.05); // fine enough to see how long the worker takes
  }
}

export function preview() {
  const text = 'Preview <strong>text</strong> with <a href="https://a.test" title="t">link</a>';
  const data = graphql("preview", PREVIEW_QUERY, { text });
  if (data) contentOk.add(typeof data.previewText === "string");
}

// An open tab: a page of the list and maybe one thread with its root unfolded.
function openTab(index) {
  const [page, ordering, row] = view(index);
  const data = graphql("list", LIST_QUERY, listVariables(page, ordering), "warmup");
  const rows = data ? data.comments.results : [];
  const root = row !== null && rows[row] ? rows[row].id : null;
  if (root !== null) replies(root, "thread", "warmup");
  // Comments heard of and the time they were heard, until the delayed reload shows them.
  return { page, ordering, root, waiting: new Map() };
}

// The viewport message of socket.js and App.jsx.
function viewport(tab) {
  return JSON.stringify({
    type: "viewport",
    page: tab.page,
    pageSize: PAGE_SIZE,
    ordering: tab.ordering,
    thread: tab.root,
    branches: tab.root === null ? [] : [{ id: tab.root, level: 0 }],
  });
}

function shown(rows, heard, fresh) {
  for (const [id, heardAt] of heard) {
    const row = rows.find((c) => c.id === id);
    fresh.add(Boolean(row));
    const sent = row ? sentAt(row.text) : null;
    if (!sent) continue;
    wsDelivery.add(heardAt - sent);
    visible.add(Date.now() - sent);
  }
}

function reloadList(tab) {
  const heard = tab.waiting;
  tab.waiting = new Map();
  const request = body(LIST_QUERY, listVariables(tab.page, tab.ordering));
  http.asyncRequest("POST", GRAPHQL_URL, request, params("reload", "live")).then((res) => {
    const data = dataOf(res, "reload");
    if (!data) return;
    contentOk.add(listOk(data.comments));
    shown(data.comments.results, heard, freshList);
  });
}

// What App.jsx does on events: a new top-level comment reloads the page after a random
// delay, a reply reloads the branch it went to if that branch is unfolded, and a new reply
// count is shown in place without a request.
function react(tab, event, heardAt) {
  if (event.type !== "comment.created") return;
  if (event.parentId === null) {
    if (!tab.waiting.size) setTimeout(() => reloadList(tab), rand() * LIVE_RELOAD_MAX_DELAY_MS);
    tab.waiting.set(event.id, heardAt);
    return;
  }
  if (event.parentId !== tab.root) return;
  const request = body(REPLIES_QUERY, { parentId: tab.root });
  http.asyncRequest("POST", GRAPHQL_URL, request, params("reload-thread", "live")).then((res) => {
    const data = dataOf(res, "reload-thread");
    if (!data) return;
    contentOk.add(repliesOk(data.replies, tab.root));
    shown(data.replies, new Map([[event.id, heardAt]]), freshThread);
  });
}

export function listen() {
  const index = exec.scenario.iterationInTest;
  const tab = openTab(index);
  const front = showsFront(index);
  const ws = new WebSocket(`${BASE_URL.replace(/^http/, "ws")}/ws/comments/`, null, {
    headers: { ...headers, Origin: ORIGIN },
  });
  ws.onopen = () => ws.send(viewport(tab));
  ws.onmessage = (message) => {
    const heardAt = Date.now();
    const event = JSON.parse(message.data);
    wsMessages.add(1);
    if (front && event.type === "comment.created" && event.parentId === null) wsFrontEvents.add(1);
    if (index < VIEWERS) react(tab, event, heardAt);
  };
  ws.onerror = () => wsErrors.add(1);
  // Stay connected until the HTTP scenarios have finished.
  setTimeout(() => ws.close(), (seconds(WARMUP) + seconds(DURATION) + 5) * 1000);
}

export function handleSummary(data) {
  return summarize(data, {
    suite_version: SUITE_VERSION,
    profile: PROFILE,
    rate: RATE,
    duration: DURATION,
    warmup: WARMUP,
    ws_clients: WS_CLIENTS,
    ws_front: frontConnections,
    viewers: VIEWERS,
    seed: SEED,
    slo_p95_ms: SLO_P95,
    base_url: BASE_URL,
    mix: MIX,
    out: env("SUMMARY_OUT", "summary.json"),
  });
}
