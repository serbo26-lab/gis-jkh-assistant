const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "../browser-extension/background.js"), "utf8"
);
const manifest = JSON.parse(fs.readFileSync(
  path.join(__dirname, "../browser-extension/manifest.json"), "utf8"
));
const patterns = [
  "https://my.dom.gosuslugi.ru/*",
  "https://lk.dom.gosuslugi.ru/*"
];

function helper(tabs, focusedWindowId = null) {
  const queries = [];
  const executions = [];
  const context = vm.createContext({
    chrome: {
      windows: { getLastFocused: async () => ({ id: focusedWindowId }) },
      tabs: { query: async ({ url }) => {
        queries.push(Array.from(url));
        return tabs.filter(tab => url.some(pattern =>
          tab.url.startsWith(pattern.slice(0, -1))
        ));
      } },
      scripting: { executeScript: async options => {
        executions.push(options);
        return [{ result: { ok: true, result: { keptAliveAt: "test" } } }];
      } },
      alarms: { create() {}, onAlarm: { addListener() {} } }
    },
    // The desktop poll stays suspended: these tests never access a real gateway.
    fetch: () => new Promise(() => {}),
    setTimeout() {}, clearTimeout() {}, setInterval() { return 1; }, clearInterval() {}
  });
  // Start polling explicitly in dispatcher tests. An implicit bootstrap racing
  // fixture setup could otherwise consume the very command being asserted.
  vm.runInContext(source.replace(/\r?\npollLoop\(\);\r?\n/, "\n"), context);
  return { context, queries, executions };
}

test("manifest grants only the two GIS hosts and the existing local gateway", () => {
  assert.deepEqual(manifest.host_permissions, [
    ...patterns, "http://127.0.0.1:8765/*"
  ]);
  const { context } = helper([]);
  assert.equal(vm.runInContext("HELPER_CODE_VERSION", context), manifest.version);
});

test("identifies the helper version on the actual command polling request", async () => {
  const { context } = helper([]);
  const requests = [];
  context.fetch = async (url, options) => { requests.push({ url, options }); return { ok: true }; };
  await vm.runInContext('localFetch("/next")', context);
  assert.equal(requests[0].options.headers["X-GIS-JKH-Helper-Version"], manifest.version);
  assert.ok(requests[0].options.headers["X-GIS-JKH-Helper-Instance"]);
});

test("a browser without a GIS tab never takes a desktop command", async () => {
  const { context } = helper([]);
  const paths = [];
  context.fetch = async (url) => { paths.push(url); return { ok: true, status: 200, json: async () => ({ idle: true }) }; };
  await vm.runInContext("pollLoop()", context);
  assert.ok(paths.some(url => url.endsWith("/hello")));
  assert.equal(paths.some(url => url.endsWith("/next")), false);
});

for (const host of ["my.dom.gosuslugi.ru", "lk.dom.gosuslugi.ru"]) {
  test(`finds an authorized tab and keeps its session alive on ${host}`, async () => {
    const { context, queries, executions } = helper([
      { id: 42, active: true, url: `https://${host}/workplanning` }
    ]);
    assert.equal((await vm.runInContext("findGisTab()", context)).id, 42);
    assert.deepEqual(queries.at(-1), patterns);
    // Avoid the final heartbeat's suspended fetch while exercising the actual
    // keepalive dispatcher, target selection and success/error state.
    vm.runInContext("hello = async () => true", context);
    await vm.runInContext("maybeKeepSessionAlive()", context);
    assert.equal(executions.length, 1);
    assert.equal(executions[0].target.tabId, 42);
    assert.equal(executions[0].world, "MAIN");
    assert.equal(executions[0].args[0].type, "keep_alive");
    assert.ok(vm.runInContext("lastKeepAliveSuccessMs", context));
    assert.equal(vm.runInContext("keepAliveError", context), null);
    await vm.runInContext("maybeKeepSessionAlive()", context);
    assert.equal(executions.length, 1, "does not repeat before the four minute interval");
  });
}

test("prefers the active GIS tab when both domains are open", async () => {
  const { context } = helper([
    { id: 1, active: false, url: "https://lk.dom.gosuslugi.ru/workplanning" },
    { id: 2, active: true, url: "https://my.dom.gosuslugi.ru/workplanning" }
  ]);
  assert.equal((await vm.runInContext("findGisTab()", context)).id, 2);
});

test("prefers the active GIS tab in the last focused browser window", async () => {
  const { context } = helper([
    { id: 1, active: true, windowId: 10, url: "https://lk.dom.gosuslugi.ru/workplanning" },
    { id: 2, active: true, windowId: 20, url: "https://my.dom.gosuslugi.ru/workplanning" }
  ], 20);
  assert.equal((await vm.runInContext("findGisTab()", context)).id, 2);
  assert.equal((await vm.runInContext(
    'findGisTab({type: "get_current_organization", payload: {}})', context
  )).id, 2);
});

test("ambiguous organization import requires an active GIS tab in the focused window", async () => {
  const tabs = [
    { id: 1, active: true, windowId: 10, url: "https://lk.dom.gosuslugi.ru/workplanning" },
    { id: 2, active: true, windowId: 20, url: "https://my.dom.gosuslugi.ru/workplanning" }
  ];
  for (const focused of [null, 30]) {
    const { context, executions } = helper(tabs, focused);
    await assert.rejects(vm.runInContext(
      'executeInGisTab({type: "get_current_organization", payload: {}})', context
    ), /Сделайте вкладку нужной УО активной/);
    assert.equal(executions.length, 0);
  }
});

function routedHelper(tabs, organizations, focused = 10) {
  const state = helper(tabs, focused);
  state.context.chrome.scripting.executeScript = async options => {
    state.executions.push(options);
    if (options.args[0].type === "get_current_organization") {
      const organization = organizations.get(options.target.tabId);
      return [{ result: organization
        ? { ok: true, result: { organization } }
        : { ok: false, error: "Session expired" } }];
    }
    return [{ result: { ok: true, result: { done: true } } }];
  };
  vm.runInContext("hello = async () => true", state.context);
  return state;
}

const orgA = { ogrn: "1022600000000", organizationRootGuid: "root-a" };
const orgB = { ogrn: "1022600000001", organizationRootGuid: "root-b" };
const twoTabs = () => [
  { id: 1, active: true, windowId: 10, url: "https://lk.dom.gosuslugi.ru/workplanning" },
  { id: 2, active: true, windowId: 20, url: "https://my.dom.gosuslugi.ru/workplanning" }
];

test("routes successive commands for two organizations to their respective tabs", async () => {
  const { context, executions } = routedHelper(twoTabs(), new Map([[1, orgA], [2, orgB]]));
  for (const expected of [orgB, orgA, orgB]) {
    context.command = { type: "save_work_fixation", payload: { organizationContext: expected } };
    await vm.runInContext("executeInGisTab(command)", context);
    assert.equal(executions.at(-1).target.tabId, expected === orgA ? 1 : 2);
  }
});

test("keeps the confirmed organization tab when browser focus changes", async () => {
  const { context, executions } = routedHelper(twoTabs(), new Map([[1, orgA], [2, orgA]]));
  context.command = { type: "approve_work_list", payload: { organizationContext: orgA } };
  await vm.runInContext("executeInGisTab(command)", context);
  context.chrome.windows.getLastFocused = async () => ({ id: 20 });
  await vm.runInContext("executeInGisTab(command)", context);
  assert.equal(executions.at(-1).target.tabId, 1);
});

test("revalidates a pinned tab and finds another tab after switching its organization", async () => {
  const organizations = new Map([[1, orgA], [2, orgA]]);
  const { context, executions } = routedHelper(twoTabs(), organizations);
  context.command = { type: "save_work_plan", payload: { organizationContext: orgA } };
  await vm.runInContext("executeInGisTab(command)", context);
  organizations.set(1, orgB);
  await vm.runInContext("executeInGisTab(command)", context);
  assert.equal(executions.at(-1).target.tabId, 2);
});

for (const actual of [orgB, { ...orgA, organizationRootGuid: "different-root" }, null]) {
  test(`refuses to send to a missing or mismatched organization: ${JSON.stringify(actual)}`, async () => {
    const { context, executions } = routedHelper(twoTabs(), new Map([[1, actual], [2, orgB]]));
    context.command = { type: "approve_quality_assessment", payload: { organizationContext: orgA } };
    await assert.rejects(vm.runInContext("executeInGisTab(command)", context), /Не найдена авторизованная вкладка/);
    assert.ok(executions.every(options => options.args[0].type === "get_current_organization"));
  });
}

test("rediscovers a confirmed organization's tab after it is closed", async () => {
  const tabs = twoTabs();
  const { context, executions } = routedHelper(tabs, new Map([[1, orgA], [2, orgA]]));
  context.command = { type: "save_work_plan", payload: { organizationContext: orgA } };
  await vm.runInContext("executeInGisTab(command)", context);
  tabs.splice(0, 1);
  await vm.runInContext("executeInGisTab(command)", context);
  assert.equal(executions.at(-1).target.tabId, 2);
});

test("does not return data for local application after the tab switches organization", async () => {
  const organizations = new Map([[1, orgA], [2, orgB]]);
  const { context } = routedHelper(twoTabs(), organizations);
  const execute = context.chrome.scripting.executeScript;
  context.chrome.scripting.executeScript = async options => {
    const result = await execute(options);
    if (options.args[0].type === "load_work_plan") organizations.set(1, orgB);
    return result;
  };
  context.command = { type: "load_work_plan", payload: { organizationContext: orgA } };
  await assert.rejects(vm.runInContext("executeInGisTab(command)", context), /изменилась во время операции/);
});

test("renews both organization sessions instead of only the active window", async () => {
  const { context, executions } = helper(twoTabs(), 10);
  vm.runInContext("hello = async () => true", context);
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.deepEqual(executions.map(options => options.target.tabId), [1, 2]);
});

test("does not treat the public portal or unrelated hosts as a GIS session", async () => {
  const { context, executions } = helper([
    { id: 1, active: true, url: "https://dom.gosuslugi.ru/" },
    { id: 2, active: false, url: "https://my.dom.gosuslugi.ru.example.org/" }
  ]);
  assert.equal(await vm.runInContext("findGisTab()", context), null);
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 0);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), null);
});

test("reports keepalive failure without claiming a successful renewal", async () => {
  const { context } = helper([
    { id: 42, active: true, url: "https://my.dom.gosuslugi.ru/workplanning" }
  ]);
  context.chrome.scripting.executeScript = async () => [{
    result: { ok: false, error: "Session expired" }
  }];
  vm.runInContext("hello = async () => true", context);
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), null);
  assert.equal(vm.runInContext("keepAliveError", context), "Session expired");
});

function renewalWithRealGreeting(http, tabs = [twoTabs()[0]]) {
  const state = helper(tabs);
  const page = organizationPage(http);
  page.document.body.dispatchEvent = () => {};
  page.document.dispatchEvent = () => {};
  page.document.querySelectorAll = () => [];
  page.Event = class Event {};
  state.context.now = Date.parse("2026-10-18T12:00:00Z");
  state.context.previousSuccess = Date.parse("2026-10-18T11:56:00Z");
  vm.runInContext("Date.now = () => now; lastKeepAliveSuccessMs = previousSuccess", state.context);
  state.context.chrome.scripting.executeScript = async options => {
    state.executions.push(options);
    assert.equal(options.args[0].type, "keep_alive");
    page.command = options.args[0];
    return [{ result: await vm.runInContext("runGisCommand(command)", page) }];
  };
  return { ...state, page };
}

test("a stalled Angular echo releases renewal and the next attempt can recover", async () => {
  let calls = 0;
  const { context, page } = renewalWithRealGreeting(() =>
    ++calls === 1 ? new Promise(() => {}) : Promise.resolve({ data: {} })
  );
  const timers = new Map();
  let next = 0;
  page.setTimeout = (callback, delay) => { timers.set(++next, { callback, delay }); return next; };
  page.clearTimeout = id => timers.delete(id);
  context.fetch = async () => ({ ok: true });
  const attempt = vm.runInContext("maybeKeepSessionAlive()", context);
  await new Promise(resolve => setImmediate(resolve));
  const watchdog = [...timers.values()].find(timer => timer.delay === 15000);
  assert.ok(watchdog, "echo needs a native deadline independent of Angular's timeout");
  watchdog.callback();
  await attempt;
  assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 0);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), context.previousSuccess);
  assert.match(vm.runInContext("keepAliveError", context), /не ответил/);
  context.now += 15000;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(calls, 2);
  assert.equal(vm.runInContext("keepAliveError", context), null);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), context.now);
  assert.equal(timers.size, 0);
});

test("a stalled organization probe keeps greetings alive and never dispatches a write", async () => {
  const { context, executions } = routedHelper([twoTabs()[0]], new Map([[1, orgA]]));
  const timers = new Map();
  const intervals = new Map();
  let next = 0;
  let greetCount = 0;
  let releaseProbe;
  context.setTimeout = (callback, delay) => { timers.set(++next, { callback, delay }); return next; };
  context.clearTimeout = id => timers.delete(id);
  context.setInterval = callback => { intervals.set(++next, callback); return next; };
  context.clearInterval = id => intervals.delete(id);
  context.greet = async () => { greetCount++; return true; };
  vm.runInContext("hello = greet", context);
  const normalExecution = context.chrome.scripting.executeScript;
  context.chrome.scripting.executeScript = options => {
    executions.push(options);
    return new Promise(resolve => { releaseProbe = resolve; });
  };
  context.command = { type: "save_work_plan", payload: { organizationContext: orgA } };
  const attempt = vm.runInContext("executeInGisTab(command)", context);
  const rejected = assert.rejects(attempt, /проверке УО/);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(intervals.size, 1, "heartbeat must start before tab routing");
  await [...intervals.values()][0]();
  assert.equal(greetCount, 1);
  const watchdog = [...timers.values()].find(timer => timer.delay === 20000);
  assert.ok(watchdog);
  watchdog.callback();
  await rejected;
  assert.equal(intervals.size, 0);
  assert.equal(executions.length, 1);
  await assert.rejects(vm.runInContext("executeInGisTab(command)", context), /проверке УО/);
  assert.equal(executions.length, 1, "do not accumulate injections into a frozen page");
  assert.ok(executions.every(options => options.args[0].type === "get_current_organization"));
  releaseProbe([{ result: { ok: true, result: { organization: orgA } } }]);
  await new Promise(resolve => setImmediate(resolve));
  context.chrome.scripting.executeScript = normalExecution;
  await vm.runInContext("executeInGisTab(command)", context);
  assert.equal(executions.filter(options => options.args[0].type === "save_work_plan").length, 1);
  assert.equal(timers.size, 0);
});

test("a stalled local greeting does not retain an otherwise completed command", async () => {
  const { context } = helper([twoTabs()[0]]);
  let completed = false;
  const attempt = vm.runInContext('executeInGisTab({type: "ping", payload: {}})', context)
    .then(() => { completed = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(completed, true);
  await attempt;
});

test("an organization routing timeout returns an error and schedules the next desktop poll", async () => {
  const { context, executions } = helper([twoTabs()[0]]);
  const timers = new Map();
  const results = [];
  let next = 0;
  context.setTimeout = (callback, delay) => { timers.set(++next, { callback, delay }); return next; };
  context.clearTimeout = id => timers.delete(id);
  vm.runInContext("lastKeepAliveAttemptMs = Date.now()", context);
  context.chrome.scripting.executeScript = options => {
    executions.push(options);
    return new Promise(() => {});
  };
  context.fetch = async (url, options) => {
    if (url.endsWith("/next")) return { status: 200, json: async () => ({
      id: "routed-write", type: "save_work_plan", payload: { organizationContext: orgA }
    }) };
    if (url.endsWith("/result")) results.push(JSON.parse(options.body));
    return { ok: true };
  };
  const poll = vm.runInContext("pollLoop()", context);
  await new Promise(resolve => setImmediate(resolve));
  [...timers.values()].find(timer => timer.delay === 20000).callback();
  await poll;
  assert.equal(results.length, 1);
  assert.equal(results[0].id, "routed-write");
  assert.equal(results[0].ok, false);
  assert.match(results[0].error, /проверке УО/);
  assert.ok([...timers.values()].some(timer => timer.delay === 1000));
  assert.ok(executions.every(options => options.args[0].type === "get_current_organization"));
});

test("a stalled identity request inside the page cannot proceed to a GIS write", async () => {
  const requests = [];
  const context = organizationPage(config => {
    requests.push(config);
    return new Promise(() => {});
  });
  const timers = new Map();
  let next = 0;
  context.setTimeout = (callback, delay) => { timers.set(++next, { callback, delay }); return next; };
  context.clearTimeout = id => timers.delete(id);
  context.command = { type: "cancel_work_list", payload: {
    workListGuid: "list-a", organizationContext: orgA
  } };
  const attempt = vm.runInContext("runGisCommand(command)", context);
  await new Promise(resolve => setImmediate(resolve));
  const watchdog = [...timers.values()].find(timer => timer.delay === 15000);
  assert.ok(watchdog);
  watchdog.callback();
  const reply = await attempt;
  assert.equal(reply.ok, false);
  assert.match(reply.error, /не ответил при проверке УО/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "GET");
  assert.equal(requests[0].url, "/ppa/api/rest/services/ppa/organizations/details");
  assert.equal(timers.size, 0);
});

for (const succeeds of [true, false]) {
  test(`publishes the completed echo ${succeeds ? "recovery" : "failure"} without waiting for the next poll`, async () => {
    const echoRequests = [];
    const { context, executions } = renewalWithRealGreeting(async config => {
      echoRequests.push(config);
      if (!succeeds) throw { status: 503, data: { message: "Echo temporarily unavailable" } };
      return { data: {} };
    });
    context.previousError = succeeds ? "Previous renewal failed" : null;
    vm.runInContext("keepAliveError = previousError", context);
    const greetings = [];
    context.fetch = async (url, options) => {
      assert.ok(url.endsWith("/hello"), "the renewal result must not depend on /next");
      greetings.push(JSON.parse(options.body));
      return { ok: true };
    };
    await vm.runInContext("hello()", context);
    await vm.runInContext("maybeKeepSessionAlive()", context);
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(executions.length, 1);
    assert.equal(echoRequests.length, 1);
    assert.equal(echoRequests[0].url, "/auth/api/rest/services/auth/echo/");
    assert.equal(greetings.length, 2, "publish once after the aggregate result, not once per tab");
    assert.equal(greetings[0].sessionKeepAliveError, context.previousError);
    assert.equal(greetings[1].sessionKeepAliveError, succeeds ? null : "Echo temporarily unavailable");
    assert.equal(greetings[1].lastKeepAliveSuccessAt,
      succeeds ? "2026-10-18T12:00:00.000Z" : "2026-10-18T11:56:00.000Z");
    assert.equal(vm.runInContext("keepAliveInFlight", context), null);
  });
}

test("one multi-tab renewal publishes one aggregate greeting instead of one per tab", async () => {
  const tabs = [
    ...twoTabs(),
    { id: 3, active: false, url: "https://my.dom.gosuslugi.ru/workplanning" }
  ];
  const { context, executions } = renewalWithRealGreeting(async () => ({ data: {} }), tabs);
  vm.runInContext('keepAliveError = "Old warning"', context);
  const greetings = [];
  context.fetch = async (url, options) => {
    assert.ok(url.endsWith("/hello"));
    greetings.push(JSON.parse(options.body));
    return { ok: true };
  };
  await vm.runInContext("maybeKeepSessionAlive()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(executions.length, 3);
  assert.equal(greetings.length, 1);
  assert.equal(greetings[0].sessionKeepAliveError, null);
  assert.equal(greetings[0].lastKeepAliveSuccessAt, "2026-10-18T12:00:00.000Z");
});

test("a pending local greeting cannot turn a successful GIS echo into a renewal timeout", async () => {
  const { context } = renewalWithRealGreeting(async () => ({ data: {} }));
  const timers = new Map();
  const greetings = [];
  let nextTimer = 0;
  let releaseGreeting;
  context.setTimeout = (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; };
  context.clearTimeout = id => timers.delete(id);
  context.fetch = async (url, options) => {
    assert.ok(url.endsWith("/hello"));
    greetings.push(JSON.parse(options.body));
    return new Promise(resolve => { releaseGreeting = resolve; });
  };
  let settled = false;
  const attempt = vm.runInContext("maybeKeepSessionAlive()", context).then(() => { settled = true; });
  await new Promise(resolve => setImmediate(resolve));
  // Exercise a deadline if the implementation incorrectly includes /hello in
  // its GIS deadline. Correct renewal has already cancelled that timer.
  context.now += 25000;
  for (const timer of Array.from(timers.values())) if (timer.delay === 25000) timer.callback();
  await new Promise(resolve => setImmediate(resolve));
  try {
    assert.equal(vm.runInContext("keepAliveError", context), null);
    assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), Date.parse("2026-10-18T12:00:00Z"));
    assert.equal(settled, true, "loopback delivery must not retain the renewal single-flight lock");
    assert.equal(vm.runInContext("keepAliveInFlight", context), null);
    assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 0);
    assert.equal(timers.size, 0);
    assert.equal(greetings.length, 1);
    assert.equal(greetings[0].sessionKeepAliveError, null);
    assert.equal(greetings[0].lastKeepAliveSuccessAt, "2026-10-18T12:00:00.000Z");
  } finally {
    releaseGreeting({ ok: true });
    await attempt;
  }
});

test("a stale duplicate tab does not override a confirmed live session", async () => {
  const { context } = helper(twoTabs());
  vm.runInContext("hello = async () => true", context);
  context.chrome.scripting.executeScript = async options => [{ result:
    options.target.tabId === 1
      ? { ok: true, result: { keptAliveAt: "test" } }
      : { ok: false, error: "Old page has no Angular injector" }
  }];
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.ok(vm.runInContext("lastKeepAliveSuccessMs", context));
  assert.equal(vm.runInContext("keepAliveError", context), null);
});

test("keeps the error when every GIS tab fails renewal", async () => {
  const { context } = helper(twoTabs());
  vm.runInContext("hello = async () => true", context);
  context.chrome.scripting.executeScript = async options => [{
    result: { ok: false, error: `Session expired in tab ${options.target.tabId}` }
  }];
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), null);
  assert.match(vm.runInContext("keepAliveError", context), /tab 1; .*tab 2/);
});

test("retries a transient renewal failure after fifteen seconds, then returns to four minutes", async () => {
  const { context, executions } = helper([twoTabs()[0]]);
  vm.runInContext("hello = async () => true", context);
  context.now = 1000000;
  vm.runInContext("Date.now = () => now", context);
  context.chrome.scripting.executeScript = async options => {
    executions.push(options);
    return [{ result: executions.length === 1
      ? { ok: false, error: "Page is loading" }
      : { ok: true, result: { keptAliveAt: "test" } } }];
  };
  await vm.runInContext("maybeKeepSessionAlive()", context);
  context.now += 14999;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 1);
  context.now += 1;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 2);
  assert.equal(vm.runInContext("keepAliveError", context), null);
  context.now += 15000;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 2);
});

test("recovers from the observed removed-frame renewal error after navigation and dispatches a desktop import", async () => {
  const tabs = [{ id: 42, active: true, url: "https://my.dom.gosuslugi.ru/workplanning" }];
  const { context, executions } = helper(tabs);
  context.now = Date.parse("2026-10-18T07:08:16Z");
  context.previousSuccess = Date.parse("2026-10-18T07:06:08.887Z");
  vm.runInContext("Date.now = () => now; lastKeepAliveSuccessMs = previousSuccess", context);
  const timers = new Map();
  let nextTimer = 0;
  let newDocumentAvailable = false;
  let commandSent = false;
  const results = [];
  const greetings = [];
  context.setTimeout = (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; };
  context.clearTimeout = id => timers.delete(id);
  context.chrome.scripting.executeScript = async options => {
    executions.push(options);
    if (options.args[0].type === "keep_alive") {
      if (!newDocumentAvailable) throw new Error("Frame with ID 0 was removed.");
      return [{ result: { ok: true, result: { keptAliveAt: "2026-10-18T07:08:31Z" } } }];
    }
    assert.equal(options.target.tabId, 42);
    assert.equal(options.args[0].type, "load_work_plan");
    assert.equal(newDocumentAvailable, true);
    return [{ result: { ok: true, result: { year: 2022, items: [{ workGuid: "work-after-navigation" }] } } }];
  };
  context.fetch = async (url, options) => {
    if (url.endsWith("/hello")) greetings.push(JSON.parse(options.body));
    if (url.endsWith("/result")) results.push(JSON.parse(options.body));
    if (url.endsWith("/next") && !commandSent) {
      commandSent = true;
      return { ok: true, status: 200, json: async () => ({
        id: "plan-after-navigation", type: "load_work_plan", payload: { year: 2022 }
      }) };
    }
    return { ok: true, status: 204 };
  };

  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(vm.runInContext("keepAliveError", context), "Frame with ID 0 was removed.");
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), context.previousSuccess);
  assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 0, "a rejected old-document injection releases its tab");
  context.now += 14999;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 1);

  context.now += 1;
  newDocumentAvailable = true;
  tabs[0].url = "https://my.dom.gosuslugi.ru/workplanning?document=new";
  await vm.runInContext("pollLoop()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(executions.filter(options => options.args[0].type === "keep_alive").length, 2);
  assert.equal(results.length, 1);
  assert.equal(results[0].id, "plan-after-navigation");
  assert.equal(results[0].ok, true);
  assert.equal(results[0].result.items[0].workGuid, "work-after-navigation");
  assert.equal(vm.runInContext("keepAliveError", context), null);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), context.now);
  assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 0);

  const nextPoll = Array.from(timers.values()).find(timer => timer.delay === 1000);
  assert.ok(nextPoll, "navigation did not terminate polling");
  await nextPoll.callback();
  assert.equal(greetings.at(-1).sessionKeepAliveError, null);
  assert.equal(greetings.at(-1).lastKeepAliveSuccessAt, "2026-10-18T07:08:31.000Z");
});

test("an alarm and poll share one keepalive attempt even while the tab query is pending", async () => {
  const { context, executions } = helper([twoTabs()[0]]);
  vm.runInContext("hello = async () => true", context);
  let resolveTabs;
  context.chrome.tabs.query = () => new Promise(resolve => { resolveTabs = resolve; });
  const first = vm.runInContext("maybeKeepSessionAlive()", context);
  const second = vm.runInContext("maybeKeepSessionAlive()", context);
  resolveTabs([twoTabs()[0]]);
  await Promise.all([first, second]);
  assert.equal(executions.length, 1);
});

test("a failed tab query does not stop polling or prevent the next desktop command", async () => {
  const { context, executions } = helper([twoTabs()[0]]);
  const scheduled = [];
  const paths = [];
  const results = [];
  let queryCount = 0;
  let commandSent = false;
  context.setTimeout = (callback, delay) => { scheduled.push({ callback, delay }); return 1; };
  context.chrome.tabs.query = async () => {
    if (++queryCount === 2) throw new Error("Temporary Edge tab query failure");
    return [twoTabs()[0]];
  };
  context.fetch = async (url, options) => {
    paths.push(url);
    if (url.endsWith("/result")) results.push(JSON.parse(options.body));
    if (url.endsWith("/next") && !commandSent) {
      commandSent = true;
      return { ok: true, status: 200, json: async () => ({ id: "after-failed-renewal", type: "ping", payload: {} }) };
    }
    return { ok: true, status: 204 };
  };
  await vm.runInContext("pollLoop()", context);
  assert.ok(paths.some(url => url.endsWith("/next")));
  assert.equal(executions[0]?.args[0].type, "ping", JSON.stringify(results));
  assert.equal(results.length, 1);
  assert.equal(results[0].id, "after-failed-renewal");
  assert.equal(results[0].ok, true);
  assert.match(vm.runInContext("keepAliveError", context), /tab query failure/);
  assert.equal(scheduled.at(-1).delay, 1000, "polling remains scheduled after the error");
  await scheduled.at(-1).callback();
  assert.equal(paths.filter(url => url.endsWith("/next")).length, 2);
});

test("a timed-out keepalive cannot overwrite a later successful renewal", async () => {
  const { context } = helper(twoTabs());
  vm.runInContext("hello = async () => true", context);
  context.now = 1000000;
  vm.runInContext("Date.now = () => now", context);
  const timers = new Map();
  let nextTimerId = 0;
  context.setTimeout = (callback, delay) => { timers.set(++nextTimerId, { callback, delay }); return nextTimerId; };
  context.clearTimeout = id => timers.delete(id);
  let finishOldAttempt;
  let attempts = 0;
  let otherTabAttempts = 0;
  context.chrome.scripting.executeScript = async options => {
    attempts++;
    if (options.target.tabId === 1) return new Promise(resolve => { finishOldAttempt = resolve; });
    return [{ result: ++otherTabAttempts === 1
      ? { ok: false, error: "Other page still loading" }
      : { ok: true, result: {} } }];
  };
  const oldAttempt = vm.runInContext("maybeKeepSessionAlive()", context);
  await new Promise(resolve => setImmediate(resolve));
  const deadline = Array.from(timers.values()).find(timer => timer.delay === 25000);
  assert.ok(deadline);
  context.now += 25000;
  deadline.callback();
  await oldAttempt;
  assert.match(vm.runInContext("keepAliveError", context), /не ответила/);
  assert.equal(timers.size, 0);
  assert.equal(vm.runInContext("lastKeepAliveAttemptMs", context), context.now);
  context.now += 14999;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(attempts, 2, "retry delay starts after the timeout, not before it");
  context.now += 1;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(attempts, 3, "only the other tab is retried while the old injection remains pending");
  assert.equal(vm.runInContext("keepAliveError", context), null);
  const successTime = vm.runInContext("lastKeepAliveSuccessMs", context);
  finishOldAttempt([{ result: { ok: false, error: "Late failure from sleeping page" } }]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 0);
  assert.equal(vm.runInContext("keepAliveError", context), null);
  assert.equal(vm.runInContext("lastKeepAliveSuccessMs", context), successTime);
});

test("repeated renewal cycles do not accumulate injections into an unresponsive tab", async () => {
  const tabs = [twoTabs()[0]];
  const { context, executions } = helper(tabs);
  vm.runInContext("hello = async () => true", context);
  context.now = 1000000;
  vm.runInContext("Date.now = () => now", context);
  let deadline;
  context.setTimeout = callback => { deadline = callback; return 1; };
  context.chrome.scripting.executeScript = async options => {
    executions.push(options);
    return new Promise(() => {});
  };
  const first = vm.runInContext("maybeKeepSessionAlive()", context);
  await new Promise(resolve => setImmediate(resolve));
  deadline();
  await first;
  for (let cycle = 0; cycle < 5; cycle++) {
    context.now += 15000;
    await vm.runInContext("maybeKeepSessionAlive()", context);
  }
  assert.equal(executions.length, 1);
  assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 1);
  tabs.length = 0;
  context.now += 15000;
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(vm.runInContext("keepAliveTabRequests.size", context), 0, "a closed/non-GIS tab releases its pending entry");
});

test("does not inject renewal scripts into discarded tabs", async () => {
  const { context, executions } = helper([{ ...twoTabs()[0], discarded: true }]);
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 0);
  assert.match(vm.runInContext("keepAliveError", context), /выгружена браузером/);
});

test("several sleeping tabs do not delay desktop commands, heartbeats or a healthy tab", async () => {
  const tabs = Array.from({ length: 5 }, (_, index) => ({
    id: index + 1, active: index === 0, url: "https://my.dom.gosuslugi.ru/workplanning"
  }));
  const { context, executions } = helper(tabs);
  const timers = new Map();
  let nextTimer = 0;
  let runningRenewals = 0;
  let peakRenewals = 0;
  const sentResults = [];
  const requests = [];
  context.setTimeout = (callback, delay) => { timers.set(++nextTimer, { callback, delay }); return nextTimer; };
  context.clearTimeout = id => timers.delete(id);
  context.chrome.scripting.executeScript = async options => {
    executions.push(options);
    if (options.args[0].type === "keep_alive") {
      peakRenewals = Math.max(peakRenewals, ++runningRenewals);
      if (options.target.tabId <= 2) return new Promise(() => {});
      runningRenewals--;
    }
    return [{ result: { ok: true, result: { done: true } } }];
  };
  context.fetch = async (url, options) => {
    requests.push(url);
    if (url.endsWith("/result")) sentResults.push(JSON.parse(options.body));
    if (url.endsWith("/next")) return {
      ok: true, status: 200,
      json: async () => ({ id: `ping-${sentResults.length}`, type: "ping", payload: {} })
    };
    return { ok: true };
  };
  vm.runInContext('keepAliveError = "Previous tab failed"', context);
  await vm.runInContext("pollLoop()", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sentResults.length, 1, "a desktop command finishes before any renewal deadline fires");
  assert.equal(sentResults[0].ok, true);
  assert.ok(peakRenewals <= 3, "renewal concurrency is bounded");
  assert.equal(executions.filter(options => options.args[0].type === "keep_alive").length, 5);
  assert.equal(vm.runInContext("keepAliveError", context), null, "a successful echo clears the old error promptly");
  const nextPoll = Array.from(timers.values()).find(timer => timer.delay === 1000);
  assert.ok(nextPoll);
  await nextPoll.callback();
  assert.equal(sentResults.length, 2, "polling continues while the same renewal attempt is pending");
  assert.ok(requests.filter(url => url.endsWith("/hello")).length >= 2);
  for (const timer of timers.values()) if (timer.delay === 25000) timer.callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(vm.runInContext("keepAliveError", context), null, "sleeping duplicates do not negate the confirmed session");
});

test("a synchronous tab API failure also releases the renewal single-flight lock", async () => {
  const { context, executions } = helper([twoTabs()[0]]);
  vm.runInContext("hello = async () => true", context);
  context.now = 1000000;
  vm.runInContext("Date.now = () => now", context);
  context.chrome.tabs.query = () => { throw new Error("Extension tab API temporarily unavailable"); };
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(vm.runInContext("keepAliveInFlight", context), null);
  context.now += 15000;
  context.chrome.tabs.query = async () => [twoTabs()[0]];
  await vm.runInContext("maybeKeepSessionAlive()", context);
  assert.equal(executions.length, 1);
  assert.equal(vm.runInContext("keepAliveError", context), null);
});

test("keepalive uses the page's authorized relative echo and harmless activity", async () => {
  const { context } = helper([]);
  const requests = [];
  const events = [];
  const element = { dispatchEvent: event => events.push(event.type) };
  context.document = {
    body: element,
    documentElement: element,
    dispatchEvent: event => events.push(event.type),
    querySelector: () => element,
    querySelectorAll: () => []
  };
  context.Event = class Event {
    constructor(type) { this.type = type; }
  };
  context.window = { angular: { element: () => ({ injector: () => ({
    get: name => {
      assert.equal(name, "$http");
      return async config => { requests.push(config); return { data: {} }; };
    }
  }) }) } };
  context.window.__GIS_JKH_ASSISTANT_PROGRESS = { commandId: "running-import" };
  const result = await vm.runInContext(
    'runGisCommand({type: "keep_alive", payload: {}})', context
  );
  assert.equal(result.ok, true);
  assert.deepEqual(events, ["mousemove", "mousemove", "scroll", "scroll"]);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "GET");
  assert.equal(requests[0].url, "/auth/api/rest/services/auth/echo/");
  assert.equal(requests[0].cache, false);
  assert.equal(requests[0].timeout, 15000);
  assert.equal(result.result.timeoutDialogWasVisible, false);
  assert.deepEqual(context.window.__GIS_JKH_ASSISTANT_PROGRESS, { commandId: "running-import" });
});

for (const status of [0, 401, 503]) {
  test(`preserves an echo network/auth/server failure instead of reporting renewal (${status})`, async () => {
    const context = organizationPage(async () => { throw { status, data: { message: `Echo failed ${status}` } }; });
    context.document.body.dispatchEvent = () => {};
    context.document.dispatchEvent = () => {};
    context.document.querySelectorAll = () => [];
    context.Event = class Event {};
    const result = await vm.runInContext('runGisCommand({type: "keep_alive", payload: {}})', context);
    assert.equal(result.ok, false);
    assert.match(result.error, new RegExp(`Echo failed ${status}`));
  });
}

for (const data of [false, "<!doctype html><html><body>Вход</body></html>", { ok: false }, { success: false }, { authenticated: false }]) {
  test(`does not report successful renewal for an HTTP-200 negative echo: ${JSON.stringify(data)}`, async () => {
    const context = organizationPage(async () => ({ data }));
    context.document.body.dispatchEvent = () => {};
    context.document.dispatchEvent = () => {};
    context.document.querySelectorAll = () => [];
    context.Event = class Event {};
    const result = await vm.runInContext('runGisCommand({type: "keep_alive", payload: {}})', context);
    assert.equal(result.ok, false);
    assert.match(result.error, /не подтвердила сессию/);
  });
}

test("continues a visible native timeout dialog before the echo, ignoring hidden or disabled copies", async () => {
  const calls = [];
  const context = organizationPage(async config => {
    calls.push(config.url);
    assert.deepEqual(calls, ["continue", "/auth/api/rest/services/auth/echo/"]);
    return { data: {} };
  });
  context.document.body.dispatchEvent = () => {};
  context.document.dispatchEvent = () => {};
  context.Event = class Event {};
  context.document.querySelectorAll = () => [
    { textContent: "Продолжить работу", getClientRects: () => [], click: () => assert.fail("hidden button") },
    { textContent: "Продолжить работу", disabled: true, getClientRects: () => [{}], click: () => assert.fail("disabled button") },
    { textContent: "Продолжить работу", getClientRects: () => [{}], click: () => calls.push("continue") }
  ];
  const result = await vm.runInContext('runGisCommand({type: "keep_alive", payload: {}})', context);
  assert.equal(result.ok, true, result.error);
  assert.equal(result.result.timeoutDialogWasVisible, true);
});

function organizationPage(http) {
  const { context } = helper([]);
  const element = {};
  context.document = {
    body: element, documentElement: element, querySelector: () => element
  };
  context.window = { angular: { element: () => ({ injector: () => ({
    get: name => { assert.equal(name, "$http"); return http; }
  }) }) } };
  return context;
}

test("rechecks organization inside the page before dispatching a command", async () => {
  const requests = [];
  const context = organizationPage(async config => {
    requests.push(config);
    return { data: { guid: "version-b", registryOrganizationRootEntityGuid: "root-b",
      shortName: "УО Б", ogrn: orgB.ogrn } };
  });
  context.command = { type: "cancel_work_list", payload: {
    workListGuid: "list-a", organizationContext: orgA
  } };
  const reply = await vm.runInContext("runGisCommand(command)", context);
  assert.equal(reply.ok, false);
  assert.match(reply.error, /не совпадает с выбранной УО/);
  assert.ok(requests.every(config => config.method === "GET" &&
    config.url === "/ppa/api/rest/services/ppa/organizations/details"));
});

test("stops a write when the organization changes after dispatch starts", async () => {
  const requests = [];
  let identityChecks = 0;
  const context = organizationPage(async config => {
    requests.push(config);
    if (config.url !== "/ppa/api/rest/services/ppa/organizations/details") {
      return { data: { guid: "list-a", status: "APPROVED" } };
    }
    if (++identityChecks === 1) {
      return { data: { guid: "version-a", registryOrganizationRootEntityGuid: "root-a",
        shortName: "УО А", ogrn: orgA.ogrn } };
    }
    return { data: { guid: "version-b", registryOrganizationRootEntityGuid: "root-b",
      shortName: "УО Б", ogrn: orgB.ogrn } };
  });
  context.command = { type: "cancel_work_list", payload: {
    workListGuid: "list-a", organizationContext: orgA
  } };
  const reply = await vm.runInContext("runGisCommand(command)", context);
  assert.equal(reply.ok, false);
  assert.match(reply.error, /не совпадает с выбранной УО/);
  assert.ok(requests.every(config => config.method === "GET"));
  assert.equal(identityChecks, 2);
});

test("executes a request when the organization still matches the selected context", async () => {
  const requests = [];
  const context = organizationPage(async config => {
    requests.push(config);
    return { data: config.url === "/ppa/api/rest/services/ppa/organizations/details"
      ? { guid: "version-a", registryOrganizationRootEntityGuid: "ROOT-A",
          shortName: "УО А", ogrn: orgA.ogrn }
      : { workPlan: null } };
  });
  context.command = { type: "load_work_plan", payload: {
    workListGuid: "list-a", year: 2020, organizationContext: orgA
  } };
  const reply = await vm.runInContext("runGisCommand(command)", context);
  assert.equal(reply.ok, true);
  assert.equal(reply.result.found, false);
  assert.equal(requests.filter(config => config.method === "POST").length, 1);
});

test("plan import preserves a foreign-year date for desktop validation", async () => {
  const requests = [];
  const context = organizationPage(async config => {
    requests.push(config);
    assert.equal(config.url, "/workplanning/api/rest/services/search/workplans");
    return { data: { workPlan: {
      guid: "historical-plan", workListGuid: "historical-list", year: 2022,
      workPlanItems: [{ sortIndex: 1, work: { guid: "work", workName: "Test work" },
        monthlyWorks: [{ monthYear: { month: 3, year: 2022 }, count: 1,
          dates: [{ date: "01.01.2001 00:00:00:000" }] }]
      }]
    } } };
  });
  const reply = await vm.runInContext(
    'runGisCommand({type: "load_work_plan", payload: {workListGuid: "historical-list", year: 2022}})', context
  );
  assert.equal(reply.ok, true);
  assert.equal(reply.result.year, 2022);
  assert.deepEqual(JSON.parse(JSON.stringify(reply.result.items[0].monthlyWorks)), [
    { month: 3, count: 1, dates: ["2001-01-01"] }
  ]);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "POST"); // Read-only search, no save/place endpoint.
});

test("loads the authorized organization with a read-only GET and narrow result", async () => {
  const requests = [];
  const context = organizationPage(async config => {
    requests.push(config);
    // Anonymized response with the same shape as the organization information
    // page. In particular the version GUID and root GUID are distinct.
    return { data: {
      guid: "organization-version-guid", registryOrganizationRootEntityGuid: "organization-root-guid",
      fullName: "Общество с ограниченной ответственностью Пример",
      shortName: "  УК Пример  ", ogrn: "1022600000000", kpp: "260001001",
      inn: "2600000000", orgEmail: "example@example.invalid", phones: [],
      chiefFirstName: "Пример", factualAddress: { formattedAddress: "Примерная, 1" }
    } };
  });
  const reply = await vm.runInContext(
    'runGisCommand({type: "get_current_organization", payload: {}})', context
  );
  assert.equal(reply.ok, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "GET");
  assert.equal(requests[0].url, "/ppa/api/rest/services/ppa/organizations/details");
  assert.equal(requests[0].headers["State-GUID"], "/organization/information");
  assert.equal(requests[0].data, undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(reply.result)), { organization: {
    name: "УК Пример", ogrn: "1022600000000", kpp: "260001001",
    organizationGuid: "organization-version-guid", organizationRootGuid: "organization-root-guid"
  } });
});

for (const conflict of [false, true]) {
  test(`act signatory import keeps conflicting GIS records explicit (${conflict})`, async () => {
    const requests = [];
    const context = organizationPage(async config => {
      requests.push(config);
      if (config.url.endsWith("/details")) {
        assert.equal(config.method, "GET");
        return {data: {guid: "org-version", ogrn: "1022600000000",
          chiefLastName: "Иванов", chiefFirstName: "Иван", chiefMiddleName: "Иванович"}};
      }
      assert.equal(config.url, "/ppa/api/rest/services/ppa/organizations/additionalinfo");
      assert.equal(config.method, "POST");
      assert.deepEqual(JSON.parse(JSON.stringify(config.data)), {organizationGuids: ["org-version"]});
      return {data: {additionalInfos: [{chiefInfo: {fio: conflict ? "Петров Пётр Петрович" : "Иванов Иван Иванович",
        position: "Директор", email: "private@example.invalid", phones: ["private"]}}]}};
    });
    const reply = await vm.runInContext('runGisCommand({type: "get_organization_act_signatory", payload: {}})', context);
    assert.equal(reply.ok, true);
    assert.equal(requests.length, 2);
    assert.equal(reply.result.ogrn, "1022600000000");
    assert.equal(reply.result.candidates.length, conflict ? 2 : 1);
    assert.equal(reply.result.candidates[0].position, conflict ? "" : "Директор");
    assert.equal(JSON.stringify(reply).includes("private"), false);
  });
}

test("uses full name for an organization without a short name", async () => {
  const context = organizationPage(async () => ({ data: {
    guid: "version", registryOrganizationRootEntityGuid: "root",
    fullName: "Полное наименование", shortName: "", ogrn: "1022600000000", kpp: null
  } }));
  const reply = await vm.runInContext(
    'runGisCommand({type: "get_current_organization", payload: {}})', context
  );
  assert.equal(reply.ok, true);
  assert.equal(reply.result.organization.name, "Полное наименование");
  assert.equal(reply.result.organization.kpp, null);
});

for (const status of [401, 403]) {
  test(`organization load explains an expired/unauthorized session (${status})`, async () => {
    const context = organizationPage(async () => { throw { status }; });
    const reply = await vm.runInContext(
      'runGisCommand({type: "get_current_organization", payload: {}})', context
    );
    assert.equal(reply.ok, false);
    assert.match(reply.error, /Войдите в личный кабинет/);
  });
}

for (const data of ["<html>Sign in</html>", {}, { shortName: "Пример", ogrn: "bad" }]) {
  test(`rejects an organization reply without a valid authorized identity: ${JSON.stringify(data)}`, async () => {
    const context = organizationPage(async () => ({ data }));
    const reply = await vm.runInContext(
      'runGisCommand({type: "get_current_organization", payload: {}})', context
    );
    assert.equal(reply.ok, false);
    assert.match(reply.error, /Информация об организации/);
    assert.equal(reply.result, undefined);
  });
}

// Exercise the real save dispatcher and its post-save reconciliation. Every
// HTTP/file-store response is local; unexpected requests fail the test.
function qualitySavePage(localActs, remoteActs, { existing = false, shared = false } = {}) {
  const requests = [];
  const uploads = [];
  let saved = existing;
  const rows = [{ guid: "quality-work", work: { guid: "work-a", code: "001" } }];
  if (shared) rows.push({ guid: "quality-work-b", work: { guid: "work-b", code: "002" } });
  const context = organizationPage(async config => {
    requests.push(config);
    const url = config.url;
    if (url === "/ppa/api/rest/services/ppa/organizations/details") {
      return { data: { guid: "org-version", registryOrganizationRootEntityGuid: "root-a",
        ogrn: orgA.ogrn, shortName: "УО А" } };
    }
    if (url === "/workplanning/api/rest/services/find/house/by/fias?fiasHouseCode=house-fias") {
      return { data: { guid: "house-a" } };
    }
    if (url === "/workplanning/api/rest/services/search/reportingperiods") {
      assert.equal(config.data.houseGuid, "house-a");
      return { data: { periods: [{ guid: "period-a", workListGuid: "list-a",
        period: { year: 2026, month: 6 }, archived: false }] } };
    }
    if (url === "/homemanagement/api/rest/services/houses/1/house-object") {
      return { data: { managementOrganization: { registryOrganizationRootEntityGuid: "root-a" } } };
    }
    if (url === "/workplanning/api/rest/services/search/estimations") {
      assert.equal(config.data.workReportingPeriodGuid, "period-a");
      return { data: { estimation: { guid: saved ? "estimation-a" : null, status: "DRAFT" },
        estimationList: rows } };
    }
    if (url === "/workplanning/api/rest/services/search/worklist/list-a") {
      return { data: { guid: "list-a", status: "APPROVED", workListItems: rows } };
    }
    if (url === "/workplanning/api/rest/services/checkunique/quality-act") {
      assert.equal(config.data.houseGuid, "house-a");
      assert.equal(config.data.orgRootGuid, "root-a");
      return { data: {} };
    }
    if (url.startsWith("/filestore/api/rest/services/filestore/fileDetail?fileUid=")) {
      return { data: { virusChecked: true, downloadAvailable: true, fileSize: 4 } };
    }
    if (url === "/workplanning/api/rest/services/search/quality-act") {
      assert.equal(config.data.houseGuid, "house-a");
      assert.equal(config.data.orgRootGuid, "root-a");
      const act = remoteActs.find(act => act.guid === config.data.guid);
      assert.ok(act, "exact GUID lookup must exist in the fixture");
      return { data: act };
    }
    if (url === "/workplanning/api/rest/services/save/estimation") {
      assert.equal(config.method, "POST");
      assert.equal(config.data.orgRootGuid, "root-a");
      saved = true;
      return { data: {} };
    }
    if (url === "/workplanning/api/rest/services/search/quality-acts") {
      assert.ok(rows.some(row => row.guid === config.data.estimationItemGuid));
      return { data: { actList: remoteActs } };
    }
    throw new Error(`Unexpected mock request: ${config.method} ${url}`);
  });
  Object.assign(context, { atob, Blob, FormData });
  context.fetch = async (url, options) => {
    assert.equal(url, "/filestore/api/rest/services/filestore/uploadFile");
    assert.equal(options.method, "POST");
    uploads.push(options.body.get("fileName"));
    return { ok: true, json: async () => ({ guid: `uploaded-${uploads.length}` }) };
  };
  context.command = { type: "save_quality_assessment", payload: {
    organizationContext: orgA, fiasHouseCode: "house-fias", houseObjectGuid: "house-object",
    workListGuid: "list-a", year: 2026, month: 6,
    knownEstimationGuid: existing ? "estimation-a" : null,
    items: rows.map((row, index) => ({
      localItemId: `local-work-${index}`, workGuid: row.work.guid, workCode: row.work.code, workName: "Работа",
      daysPerMonth: 30, acts: localActs
    }))
  } };
  return { context, requests, uploads };
}

function localQualityAct(id, date = "15.07.2026", updates = {}) {
  return { localActId: id, number: "7", actType: "ACCEPTANCE_ACT", regDate: date,
    periodFrom: "01.06.2026", periodTo: "30.06.2026", fileBase64: "dGVzdA==",
    fileName: `${id}.pdf`, contentType: "application/pdf", ...updates };
}

function remoteQualityAct(guid, date = "15.07.2026 00:00:00:000", updates = {}) {
  return { guid, number: "7", actType: "ACCEPTANCE_ACT", regDate: date,
    period: { from: "01.06.2026", to: "30.06.2026" },
    attachment: { contentGuid: `file-${guid}` }, ...updates };
}

async function saveQualityFixture(localActs, remoteActs, options) {
  const fixture = qualitySavePage(localActs, remoteActs, options);
  const reply = await vm.runInContext("runGisCommand(command)", fixture.context);
  assert.equal(reply.ok, true, reply.error);
  assert.ok(fixture.requests.some(request => request.url.endsWith("/save/estimation")));
  return { ...fixture, result: JSON.parse(JSON.stringify(reply.result)) };
}

for (const reversed of [false, true]) {
  test(`matches equal act numbers by registration date after save (reversed=${reversed})`, async () => {
    const remoteActs = [remoteQualityAct("earlier", "01.07.2026 00:00:00:000"),
      remoteQualityAct("later", "15.07.2026 00:00:00:000")];
    if (reversed) remoteActs.reverse();
    const { result, uploads } = await saveQualityFixture([
      localQualityAct("local-earlier", "01.07.2026"), localQualityAct("local-later")
    ], remoteActs);
    assert.deepEqual(result.acts, [
      { localActId: "local-earlier", contentGuid: "file-earlier", guid: "earlier" },
      { localActId: "local-later", contentGuid: "file-later", guid: "later" }
    ]);
    assert.equal(uploads.length, 2);
  });
}

test("act reconciliation still distinguishes number, type and both period bounds", async () => {
  const { result } = await saveQualityFixture([localQualityAct("local")], [
    remoteQualityAct("wrong-number", undefined, { number: "8" }),
    remoteQualityAct("wrong-type", undefined, { actType: "QUALITY_VIOLATION_ACT" }),
    remoteQualityAct("wrong-start", undefined, { period: { from: "02.06.2026", to: "30.06.2026" } }),
    remoteQualityAct("wrong-end", undefined, { period: { from: "01.06.2026", to: "29.06.2026" } }),
    remoteQualityAct("correct")
  ]);
  assert.equal(result.acts[0].guid, "correct");
});

test("does not choose the first of several acts with identical metadata", async () => {
  const { context, requests } = qualitySavePage([localQualityAct("local")], [
    remoteQualityAct("duplicate-a"), remoteQualityAct("duplicate-b")
  ]);
  const reply = await vm.runInContext("runGisCommand(command)", context);
  assert.equal(reply.ok, false);
  assert.match(reply.error, /однозначно сопоставить акт № 7 от 15\.07\.2026/);
  assert.equal(reply.result, undefined, "must not return an arbitrary GUID mapping");
  assert.equal(requests.filter(request => request.url.endsWith("/save/estimation")).length, 1);
});

for (const date of ["", null, "01.07.2026", "31.02.2026"]) {
  test(`does not assign an act with missing, invalid or different date (${date})`, async () => {
    const { result } = await saveQualityFixture([localQualityAct("local")], [remoteQualityAct("wrong", date)]);
    assert.deepEqual(result.acts, [{ localActId: "local", contentGuid: "uploaded-1", guid: null }]);
  });
}

for (const date of ["15.07.2026", "15.07.2026 12:30:00:000", "2026-07-15T00:00:00Z"]) {
  test(`compares calendar dates independently of API time representation (${date})`, async () => {
    const { result } = await saveQualityFixture([localQualityAct("local")], [remoteQualityAct("correct", date)]);
    assert.equal(result.acts[0].guid, "correct");
  });
}

test("an exact act GUID takes priority over a shared attachment and changed metadata", async () => {
  const { result, uploads } = await saveQualityFixture([
    localQualityAct("local", "15.07.2026", { remoteActGuid: "exact", remoteContentGuid: "shared-file" })
  ], [
    remoteQualityAct("other", undefined, { attachment: { contentGuid: "shared-file" } }),
    remoteQualityAct("exact", "01.07.2026", { number: "edited", attachment: { contentGuid: "shared-file" } })
  ]);
  assert.equal(result.acts[0].guid, "exact");
  assert.equal(uploads.length, 0);
});

test("does not fall back to metadata when a previously known act GUID is absent", async () => {
  const fixture = qualitySavePage([
    localQualityAct("local", "15.07.2026", { remoteActGuid: "known" })
  ], [remoteQualityAct("known"), remoteQualityAct("same-metadata")]);
  const http = fixture.context.window.angular.element().injector().get("$http");
  fixture.context.window.angular.element = () => ({ injector: () => ({ get: () => async config => {
    if (config.url.endsWith("/search/quality-acts")) return { data: { actList: [remoteQualityAct("same-metadata")] } };
    return http(config);
  } }) });
  const reply = await vm.runInContext("runGisCommand(command)", fixture.context);
  assert.equal(reply.ok, true, reply.error);
  assert.equal(reply.result.acts[0].guid, "known");
});

test("keeps the other uploaded file mapping when only one act can be identified", async () => {
  const { result } = await saveQualityFixture([
    localQualityAct("local-earlier", "01.07.2026"), localQualityAct("local-later")
  ], [remoteQualityAct("earlier", "01.07.2026"), remoteQualityAct("missing-date", null)]);
  assert.deepEqual(result.acts, [
    { localActId: "local-earlier", contentGuid: "file-earlier", guid: "earlier" },
    { localActId: "local-later", contentGuid: "uploaded-2", guid: null }
  ]);
});

test("matches dates while updating an existing quality draft", async () => {
  const { result } = await saveQualityFixture([localQualityAct("local")], [
    remoteQualityAct("earlier", "01.07.2026"), remoteQualityAct("later")
  ], { existing: true });
  assert.equal(result.acts[0].guid, "later");
});

test("links one newly uploaded shared act to two works without using the older namesake", async () => {
  const { result, uploads, requests } = await saveQualityFixture([localQualityAct("shared")], [
    remoteQualityAct("earlier", "01.07.2026"), remoteQualityAct("correct")
  ], { shared: true });
  assert.equal(uploads.length, 1, "shared act must be uploaded only once");
  assert.ok(result.acts.length > 0);
  assert.ok(result.acts.every(act => act.localActId === "shared" && act.guid === "correct"));
  const saves = requests.filter(request => request.url.endsWith("/save/estimation"));
  assert.equal(saves.length, 2, "first save creates the act, second links it to the other work");
  assert.equal(saves[0].data.estimationList.length, 2);
  assert.equal(saves[0].data.estimationList[0].qualityAct.length, 1);
  assert.equal(saves[0].data.estimationList[1].qualityAct.length, 0, "the draft may temporarily have a row without an act");
  assert.equal(saves[1].data.estimationList.length, 2);
  assert.ok(saves[1].data.estimationList.every(row => row.qualityAct.some(act => act.guid === "correct")));
});

for (const date of ["", "31.02.2026"]) {
  test(`does not infer identity when both dates are absent or invalid (${date})`, async () => {
    const { result } = await saveQualityFixture([localQualityAct("local", date)], [remoteQualityAct("wrong", date)]);
    assert.deepEqual(result.acts, [{ localActId: "local", contentGuid: "uploaded-1", guid: null }]);
  });
}

test("recognizes a valid leap day as the same calendar date", async () => {
  const { result } = await saveQualityFixture([localQualityAct("local", "29.02.2024")], [
    remoteQualityAct("correct", "2024-02-29T23:30:00-10:00")
  ]);
  assert.equal(result.acts[0].guid, "correct");
});
