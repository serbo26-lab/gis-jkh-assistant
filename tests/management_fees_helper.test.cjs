const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../browser-extension/background.js"), "utf8");
const base = "/agreements/api/rest/services/agreements/management/payment";
const org = {ogrn: "1022600000540", organizationRootGuid: "org-root"};
const contract = {guid: "contract-root", versionGuid: "contract-version", objectRootGuid: "object-root",
  fiasHouseGuid: "fias-house", number: "TEST", date: "2020-01-01", from: "2020-01-01", to: "2030-12-31"};
const protocol = {votingGuid: "protocol-root-old", votingVersionGuid: "protocol-version-old",
  protocolCode: "1", protocolDate: "2020-01-01", meetingType: "OWNERS", eligible: true, cancelled: false,
  fiasHouseCodes: ["fias-house"]};
const document = {contract, amount: "15.12", start: "2026-01-01", end: "2026-12-31", protocol};

function fixture(options = {}) {
  const calls = [];
  const object = {rootEntityGuid: "object-root", fiasHouseAddress: {houseRef: {code: "fias-house"}},
    managedDateStart: "01.01.2020", managedDateEnd: "31.12.2030"};
  const contractDto = {guid: "contract-root", versionInfoDTO: {guid: "contract-version", versionNumber: 1},
    agreementType: "MANAGEMENT", published: true, managementOrgRootGuid: "org-root",
    managementOrg: {ogrn: org.ogrn}, effectiveDate: "01.01.2020", endDate: "31.12.2030",
    number: "TEST", signDate: "01.01.2020", managedObjectList: [object], ...options.contract};
  const state = {records: options.records || [], protocols: options.protocols || [protocol,
    {...protocol, votingGuid: "protocol-root-new", votingVersionGuid: "protocol-version-new", protocolDate: "2025-01-01"}]};
  const http = async config => {
    calls.push(JSON.parse(JSON.stringify(config)));
    const {url, method} = config;
    if (url === "/ppa/api/rest/services/ppa/organizations/details") return {data: {
      guid: "org-version", registryOrganizationRootEntityGuid: "org-root", ogrn: org.ogrn, shortName: "TEST UO"}};
    if (url.endsWith("/documents/search")) return {data: [{...contractDto, managementOrgOgrn: org.ogrn, totalCount: 1}]};
    if (url.includes("/managements/versions/")) return {data: contractDto};
    if (url.includes("/protocol/search-lite")) return {data: {items: state.protocols, total: state.protocols.length}};
    if (url.startsWith(base + "/search;")) return {data: {items: state.records, totalCount: state.records.length}};
    if (url === base + "/" && method === "POST") {
      assert.equal(config.transformResponse[0]("fee-root"), "fee-root",
        "a bare UUID advertised as JSON must bypass Angular's default JSON parser");
      if (options.createError) throw new Error("TEST uncertain connection");
      state.records.push(remote({...config.data}));
      return {data: "fee-root"}; // The recorded server returns a bare UUID, not an object.
    }
    if (url === base + "/" && method === "PUT") {
      state.records[0] = remote({...config.data, lastUpdate: "updated"});
      return {data: ""};
    }
    if (url === base + "/approval") {
      if (!options.unconfirmed) state.records[0].status = "APPROVED";
      return {data: ""};
    }
    if (url === base + "/termination") {
      state.records[0].status = "TERMINATED";
      state.records[0].versionInfo = {guid: "fee-version-2", versionNumber: 2};
      return {data: ""};
    }
    if (url === base + "/fee-root") return {data: state.records[0]};
    throw new Error("Unexpected mock request: " + method + " " + url);
  };
  const context = vm.createContext({chrome: {tabs: {query: async () => []},
    alarms: {create() {}, onAlarm: {addListener() {}}}},
    fetch: () => new Promise(() => {}), setTimeout() {}, setInterval() {}, clearInterval() {}});
  vm.runInContext(source, context);
  const element = {};
  context.document = {body: element, documentElement: element, querySelector: () => element};
  context.window = {angular: {element: () => ({injector: () => ({get: () => http})})}};
  async function run(type, payload = {}) {
    context.command = {type, payload: JSON.parse(JSON.stringify({organizationContext: org,
      fiasHouseGuid: "fias-house", contract, document, ...payload}))};
    return JSON.parse(JSON.stringify(await vm.runInContext("runGisCommand(command)", context)));
  }
  return {calls, state, run};
}
function remote(updates = {}) {
  return {guid: "fee-root", lastUpdate: "revision-1", agreementRootGuid: "contract-root",
    managedObjectRootGuid: "object-root", agreementType: "MANAGEMENT", status: "DRAFT", type: "BY_PROTOCOL",
    protocolFromVoting: true, ownerVotingGuidList: [protocol.votingGuid],
    ownerVotingList: [{votingGuid: protocol.votingGuid, votingVersionGuid: protocol.votingVersionGuid,
      number: "1", date: "01.01.2020"}], managementPayment: "15.12",
    period: {from: "01.01.2026", to: "31.12.2026"}, versionInfo: {guid: "fee-version-1", versionNumber: 1}, ...updates};
}
function writes(calls) { return calls.filter(c => c.url.startsWith(base) && !c.url.includes("/search;") && c.method !== "GET"); }

test("loads the recorded agreement shape and keeps equal protocol numbers distinguishable by date and ID", async () => {
  const f = fixture({records: [remote(), remote({guid: "other-house", managedObjectRootGuid: "other-object"})]});
  const reply = await f.run("load_management_fees");
  assert.equal(reply.ok, true, reply.error);
  assert.equal(reply.result.contracts[0].objectRootGuid, "object-root");
  assert.equal(reply.result.records.length, 1);
  assert.deepEqual(reply.result.protocols.map(p => p.protocolDate), ["2020-01-01", "2025-01-01"]);
  assert.equal(writes(f.calls).length, 0);
});

test("creates only a draft using the explicitly selected older protocol, then reads the saved state", async () => {
  const f = fixture();
  const reply = await f.run("save_management_fee_project");
  assert.equal(reply.ok, true, reply.error);
  const sent = writes(f.calls);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].method, "POST");
  assert.deepEqual(sent[0].data.ownerVotingGuidList, ["protocol-root-old"]);
  assert.equal(sent[0].data.status, "DRAFT");
  assert.equal(sent[0].data.period.from, "01.01.2026");
  assert.match(sent[0].headers["State-GUID"], /BY_PROTOCOL\/kirp\/payment\/add$/);
  assert.equal(reply.result.record.status, "DRAFT");
});

for (const [name, options, payload, error] of [
  ["missing basis", {protocols: []}, {}, /протокол/],
  ["protocol from another house", {protocols: [{...protocol, fiasHouseCodes: ["other-house"]}]}, {}, /протокол/],
  ["wrong agreement organization", {contract: {managementOrg: {ogrn: "0000000000000"}}}, {}, /не соответствует/],
  ["wrong managed object", {contract: {managedObjectList: []}}, {}, /не соответствует/],
  ["no selected organization", {}, {organizationContext: null}, /Выберите УО/],
  ["out of contract dates", {}, {document: {...document, start: "2019-01-01"}}, /Период/],
  ["nonexistent date", {}, {document: {...document, end: "2026-02-30"}}, /дат/],
]) {
  test(`rejects ${name} before any fee write`, async () => {
    const f = fixture(options), reply = await f.run("save_management_fee_project", payload);
    assert.equal(reply.ok, false);
    assert.match(reply.error, error);
    assert.equal(writes(f.calls).length, 0);
  });
}

test("does not create a duplicate after a lost create response", async () => {
  const f = fixture({records: [remote()]});
  const reply = await f.run("save_management_fee_project");
  assert.equal(reply.ok, true, reply.error);
  assert.equal(reply.result.record.guid, "fee-root");
  assert.equal(writes(f.calls).length, 0);
});

test("blocks changed projects using lastUpdate without overwriting them", async () => {
  const f = fixture({records: [remote({managementPayment: "16.00", lastUpdate: "changed-elsewhere"})]});
  const reply = await f.run("save_management_fee_project", {remoteGuid: "fee-root", lastUpdate: "revision-1"});
  assert.equal(reply.ok, false);
  assert.equal(writes(f.calls).length, 0);
});

test("updates a draft through the recorded resource service's PUT endpoint", async () => {
  const f = fixture({records: [remote({managementPayment: "16.00"})]});
  const reply = await f.run("save_management_fee_project", {remoteGuid: "fee-root", lastUpdate: "revision-1"});
  assert.equal(reply.ok, true, reply.error);
  assert.equal(writes(f.calls)[0].method, "PUT");
  assert.equal(reply.result.record.managementPayment, "15.12");
});

test("approval is separate and requires a readback of APPROVED", async () => {
  for (const unconfirmed of [false, true]) {
    const f = fixture({records: [remote()], unconfirmed});
    const reply = await f.run("approve_management_fee", {remoteGuid: "fee-root", lastUpdate: "revision-1"});
    assert.equal(reply.ok, !unconfirmed, reply.error);
    assert.equal(writes(f.calls)[0].url, base + "/approval");
    assert.deepEqual(writes(f.calls)[0].data, {entityGuid: "fee-root", lastUpdate: "revision-1"});
  }
});

test("annuls fee information, preserves its identity and reads TERMINATED; never terminates a contract", async () => {
  const f = fixture({records: [remote({status: "APPROVED"})]});
  const reply = await f.run("annul_management_fee", {remoteGuid: "fee-root", lastUpdate: "revision-1", reason: "TEST reason"});
  assert.equal(reply.ok, true, reply.error);
  assert.equal(reply.result.record.status, "TERMINATED");
  assert.equal(reply.result.record.guid, "fee-root");
  assert.equal(writes(f.calls)[0].url, base + "/termination");
});

test("rejects approval after the selected protocol was revised", async () => {
  const f = fixture({records: [remote()], protocols: [{...protocol, votingVersionGuid: "revised-version"}]});
  const reply = await f.run("approve_management_fee", {remoteGuid: "fee-root", lastUpdate: "revision-1"});
  assert.equal(reply.ok, false);
  assert.equal(writes(f.calls).length, 0);
});

test("does not erase detailed fee breakdowns while updating a simplified project", async () => {
  const f = fixture({records: [remote({managementPayment: "16.00", managementWorkPaymentInfo: [{test: "keep"}]})]});
  const reply = await f.run("save_management_fee_project", {remoteGuid: "fee-root", lastUpdate: "revision-1"});
  assert.equal(reply.ok, false);
  assert.equal(writes(f.calls).length, 0);
  assert.equal(f.state.records[0].managementWorkPaymentInfo[0].test, "keep");
});
