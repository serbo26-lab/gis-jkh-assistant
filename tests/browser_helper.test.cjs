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

function helper(tabs) {
  const queries = [];
  const executions = [];
  const context = vm.createContext({
    chrome: {
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
    setTimeout() {}, setInterval() { return 1; }, clearInterval() {}
  });
  vm.runInContext(source, context);
  return { context, queries, executions };
}

test("manifest grants only the two GIS hosts and the existing local gateway", () => {
  assert.deepEqual(manifest.host_permissions, [
    ...patterns, "http://127.0.0.1:8765/*"
  ]);
  const { context } = helper([]);
  assert.equal(vm.runInContext("HELPER_CODE_VERSION", context), manifest.version);
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
  const result = await vm.runInContext(
    'runGisCommand({type: "keep_alive", payload: {}})', context
  );
  assert.equal(result.ok, true);
  assert.deepEqual(events, ["mousemove", "mousemove", "scroll", "scroll"]);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "GET");
  assert.equal(requests[0].url, "/auth/api/rest/services/auth/echo/");
  assert.equal(result.result.timeoutDialogWasVisible, false);
});
