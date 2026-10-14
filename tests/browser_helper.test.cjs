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
