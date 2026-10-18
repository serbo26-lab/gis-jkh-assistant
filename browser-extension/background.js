const LOCAL_BASE = "http://127.0.0.1:8765/api/integration";
const HELPER_HEADER = { "X-GIS-JKH-Helper": "desktop-helper" };
const GIS_MATCH = [
  "https://my.dom.gosuslugi.ru/*",
  "https://lk.dom.gosuslugi.ru/*"
];
const KEEP_ALIVE_INTERVAL_MS = 4 * 60 * 1000;
const KEEP_ALIVE_RETRY_MS = 15 * 1000;
const KEEP_ALIVE_TAB_TIMEOUT_MS = 25 * 1000;
const KEEP_ALIVE_CONCURRENCY = 3;
const KEEP_ALIVE_ALARM = "gis-jkh-session-keepalive";
const HELPER_CODE_VERSION = "0.1.36";
const organizationTabs = new Map();
const keepAliveTabRequests = new Map();
// Routing identity for this worker, not an authentication credential.
const HELPER_INSTANCE = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
let gisTabAvailable = false;

let lastKeepAliveAttemptMs = 0;
let lastKeepAliveSuccessMs = null;
let keepAliveError = null;
let keepAliveInFlight = null;

async function localFetch(path, options = {}) {
  const headers = Object.assign({}, HELPER_HEADER, {
    "X-GIS-JKH-Helper-Version": HELPER_CODE_VERSION,
    "X-GIS-JKH-Helper-Instance": HELPER_INSTANCE
  }, options.headers || {});
  return fetch(LOCAL_BASE + path, Object.assign({}, options, { headers }));
}

function organizationMatches(expected, actual) {
  const text = value => String(value || "").trim().toLowerCase();
  return Boolean(expected?.ogrn && actual?.ogrn) &&
    text(expected.ogrn) === text(actual.ogrn) &&
    (!expected.organizationRootGuid ||
      text(expected.organizationRootGuid) === text(actual.organizationRootGuid));
}

async function findGisTab(command = null) {
  const tabs = await chrome.tabs.query({ url: GIS_MATCH });
  if (!tabs.length) return null;

  let focusedWindowId = null;
  try {
    focusedWindowId = (await chrome.windows.getLastFocused()).id;
  } catch (_) {
    // An unavailable window API never chooses between ambiguous import tabs.
  }
  const focusedActive = tabs.find(tab => tab.active && tab.windowId === focusedWindowId);
  tabs.sort((a, b) => Number(b === focusedActive) - Number(a === focusedActive) ||
    Number(b.active) - Number(a.active) || (b.lastAccessed || 0) - (a.lastAccessed || 0) ||
    a.id - b.id);

  const expected = command?.payload?.organizationContext;
  if (expected) {
    if (!/^\d{13}(\d{2})?$/.test(String(expected.ogrn || ""))) {
      throw new Error("Команда не содержит корректный ОГРН выбранной управляющей организации.");
    }
    const key = `${expected.ogrn}:${expected.organizationRootGuid || ""}`;
    const pinnedId = organizationTabs.get(key);
    const candidates = [...tabs].sort((a, b) => Number(b.id === pinnedId) - Number(a.id === pinnedId));
    for (const tab of candidates) {
      try {
        const execution = await chrome.scripting.executeScript({
          target: { tabId: tab.id }, world: "MAIN", func: runGisCommand,
          args: [{ type: "get_current_organization", payload: {} }]
        });
        const reply = execution?.[0]?.result;
        if (reply?.ok && organizationMatches(expected, reply.result?.organization)) {
          organizationTabs.set(key, tab.id);
          return tab;
        }
      } catch (_) {
        // Closed and unauthorized tabs cannot be used for this organization.
      }
    }
    organizationTabs.delete(key);
    throw new Error(
      `Не найдена авторизованная вкладка ГИС ЖКХ для УО с ОГРН ${expected.ogrn}. ` +
      "Откройте её кабинет в браузере и повторите операцию."
    );
  }

  if (command?.type === "get_current_organization" && tabs.length > 1) {
    const active = tabs.filter(tab => tab.active);
    if (focusedActive) return focusedActive;
    if (focusedWindowId === null && active.length === 1) return active[0];
    throw new Error("Сделайте вкладку нужной УО активной в браузере и повторите добавление УО.");
  }
  return tabs[0];
}

async function hello() {
  try {
    const tab = await findGisTab();
    gisTabAvailable = Boolean(tab && tab.id);
    const response = await localFetch("/hello", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gisPageReady: gisTabAvailable,
        helperInstance: HELPER_INSTANCE,
        pageUrl: tab?.url || null,
        helperVersion: HELPER_CODE_VERSION,
        sessionKeepAliveEnabled: true,
        lastKeepAliveSuccessAt: lastKeepAliveSuccessMs
          ? new Date(lastKeepAliveSuccessMs).toISOString()
          : null,
        sessionKeepAliveError: keepAliveError
      })
    });
    return response.ok;
  } catch (_) {
    // Desktop application may be closed. This is expected.
    return false;
  }
}

async function sendResult(id, ok, result = null, error = null) {
  try {
    await localFetch("/result", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, ok, result, error })
    });
  } catch (_) {
    // The desktop app may have been closed while a command was running.
  }
}

async function sendProgress(id, progress) {
  if (!id || !progress || typeof progress !== "object") return;
  try {
    await localFetch("/progress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, progress })
    });
  } catch (_) {
    // Progress is advisory; the final command result remains authoritative.
  }
}

async function maybeKeepSessionAlive() {
  // Polling and the alarm can wake together after Edge resumes a sleeping tab.
  // Share one attempt, and retry transient page/network failures promptly.
  if (keepAliveInFlight) return keepAliveInFlight;
  const interval = keepAliveError ? KEEP_ALIVE_RETRY_MS : KEEP_ALIVE_INTERVAL_MS;
  if (Date.now() - lastKeepAliveAttemptMs < interval) return;

  const attempt = (async () => {
    try {
      const tabs = await chrome.tabs.query({ url: GIS_MATCH });
      const openTabIds = new Set(tabs.map(tab => tab.id));
      for (const tabId of keepAliveTabRequests.keys()) {
        if (!openTabIds.has(tabId)) keepAliveTabRequests.delete(tabId);
      }
      if (!tabs.length) return;
      lastKeepAliveAttemptMs = Date.now();
      const errors = [];
      let succeeded = false;
      async function renewTab(tab) {
        if (tab.discarded || keepAliveTabRequests.has(tab.id)) {
          errors.push(tab.discarded
            ? "Вкладка ГИС ЖКХ выгружена браузером. Откройте её для проверки сессии."
            : "Ожидается ответ предыдущей проверки сессии во вкладке ГИС ЖКХ.");
          return;
        }
        let deadline;
        try {
          const request = executeInGisTab({ type: "keep_alive", payload: {} }, tab)
            .finally(() => {
              if (keepAliveTabRequests.get(tab.id) === request) keepAliveTabRequests.delete(tab.id);
            });
          keepAliveTabRequests.set(tab.id, request);
          // executeScript may remain pending in a discarded/frozen page. This
          // read-only renewal must never stop the desktop command poll forever.
          // Keep the real request registered until it settles: the deadline
          // cannot cancel execution, so retries must not accumulate injections.
          await Promise.race([
            request,
            new Promise((_, reject) => {
              deadline = setTimeout(() => reject(new Error(
                "Вкладка ГИС ЖКХ не ответила при продлении сессии."
              )), KEEP_ALIVE_TAB_TIMEOUT_MS);
            })
          ]);
          succeeded = true;
          lastKeepAliveSuccessMs = Date.now();
          keepAliveError = null;
        } catch (error) {
          errors.push(String(error?.message || error));
        } finally {
          clearTimeout(deadline);
        }
      }
      let nextTab = 0;
      await Promise.all(Array.from(
        { length: Math.min(tabs.length, KEEP_ALIVE_CONCURRENCY) },
        async () => {
          while (nextTab < tabs.length) await renewTab(tabs[nextTab++]);
        }
      ));
      // A stale or loading duplicate tab must not overrule a confirmed session
      // in another tab. Organization routing still validates every real command.
      keepAliveError = succeeded ? null : errors.join("; ") || null;
      lastKeepAliveAttemptMs = Date.now();
    } catch (error) {
      lastKeepAliveAttemptMs = Date.now();
      keepAliveError = String(error?.message || error);
    }
    // Publish the aggregate result after updating it. Delivery to localhost is
    // advisory and must not extend a GIS request's deadline or retain its lock.
    hello();
  })();
  keepAliveInFlight = attempt;
  try {
    await attempt;
  } finally {
    keepAliveInFlight = null;
  }
}

async function executeInGisTab(command, selectedTab = null) {
  const tab = selectedTab || await findGisTab(command);
  if (!tab?.id) {
    throw new Error("Не найдена открытая вкладка личного кабинета ГИС ЖКХ.");
  }

  // Long registry operations can take several minutes. Keep the desktop-side
  // connection status fresh while the command is still executing in the GIS tab.
  const heartbeat = command?.type === "keep_alive" ? null : setInterval(() => {
    hello();
  }, 10000);

  let progressProbeRunning = false;
  let lastProgressJson = "";
  const progressProbe = command?.id
    ? setInterval(async () => {
        if (progressProbeRunning) return;
        progressProbeRunning = true;
        try {
          const probe = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            world: "MAIN",
            func: () => window.__GIS_JKH_ASSISTANT_PROGRESS || null
          });
          const progress = probe?.[0]?.result || null;
          const serialized = progress ? JSON.stringify(progress) : "";
          if (serialized && serialized !== lastProgressJson) {
            lastProgressJson = serialized;
            await sendProgress(command.id, progress);
          }
        } catch (_) {
          // The main command result will report a real error if the tab failed.
        } finally {
          progressProbeRunning = false;
        }
      }, 1000)
    : null;

  try {
    const execution = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: runGisCommand,
      args: [command]
    });

    if (!execution.length) {
      throw new Error("Не удалось выполнить команду во вкладке ГИС ЖКХ.");
    }

    const result = execution[0].result;
    if (!result || result.ok !== true) {
      throw new Error(result?.error || "ГИС ЖКХ вернул неизвестную ошибку.");
    }
    const expected = command.payload?.organizationContext;
    if (expected) {
      const check = await chrome.scripting.executeScript({
        target: { tabId: tab.id }, world: "MAIN", func: runGisCommand,
        args: [{ type: "get_current_organization", payload: {} }]
      });
      const reply = check?.[0]?.result;
      if (!reply?.ok || !organizationMatches(expected, reply.result?.organization)) {
        throw new Error(
          "Организация во вкладке ГИС ЖКХ изменилась во время операции. " +
          "Результат не применён локально. Проверьте состояние операции в кабинете выбранной УО."
        );
      }
    }
    return result.result || {};
  } finally {
    if (heartbeat) clearInterval(heartbeat);
    if (progressProbe) clearInterval(progressProbe);
    if (command?.type !== "keep_alive") await hello();
  }
}

let reconnectDelayMs = 1000;

async function pollLoop() {
  try {
    const connected = await hello();
    if (!connected) {
      // Do not also call /next when the desktop app is closed. One failed probe
      // and a short backoff keep the extension console/network log quiet.
      reconnectDelayMs = Math.min(reconnectDelayMs * 2, 10000);
      return;
    }
    reconnectDelayMs = 1000;

    // A browser without a GIS tab must not consume another browser's command.
    if (!gisTabAvailable) {
      reconnectDelayMs = 2000;
      return;
    }

    // Slow/frozen GIS tabs must not delay commands or the normal /hello rhythm.
    // The shared renewal attempt handles its own errors and remains read-only.
    maybeKeepSessionAlive().catch(() => {});
    const response = await localFetch("/next");
    if (response.status === 200) {
      const command = await response.json();
      if (command?.id && command?.type) {
        try {
          const result = await executeInGisTab(command);
          await sendResult(command.id, true, result, null);
        } catch (error) {
          await sendResult(command.id, false, null, String(error?.message || error));
        }
      }
    }
  } catch (_) {
    reconnectDelayMs = 2000;
  } finally {
    // A failed tab query/renewal must not silently terminate the polling chain.
    setTimeout(pollLoop, reconnectDelayMs);
  }
}

pollLoop();
for (const event of [chrome.tabs.onUpdated, chrome.tabs.onActivated, chrome.tabs.onRemoved]) {
  if (event?.addListener) event.addListener(() => { hello(); });
}
chrome.tabs.onRemoved?.addListener((tabId) => { keepAliveTabRequests.delete(tabId); });

chrome.alarms.create(KEEP_ALIVE_ALARM, {
  delayInMinutes: 4,
  periodInMinutes: 4
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== KEEP_ALIVE_ALARM) return;

  const connected = await hello();
  if (connected) {
    await maybeKeepSessionAlive();
  }
});


async function runGisCommand(command) {
  function readableError(error) {
    const data = error?.data;
    let validationData = data;
    if (typeof validationData === "string") {
      try { validationData = JSON.parse(validationData); } catch (_) { /* Plain-text error response. */ }
    }
    const logicType = validationData?.details?.logicExceptionType || validationData?.details?.type;
    if (error?.status === 400 && logicType === "MP_NOT_CORRESPOND_MANAGEMENT_AGR_PERIOD") {
      return "ГИС отклонила период платы: он не соответствует сроку управления домом по договору. " +
        "Обновите сведения о договоре и проверьте даты начала и окончания платы.";
    }
    const candidates = [
      data?.message,
      data?.error,
      data?.description,
      data?.detail,
      data?.errors,
      data?.violations
    ];
    for (const value of candidates) {
      if (typeof value === "string" && value.trim()) return value.trim();
      if (value && typeof value === "object") {
        try {
          const text = JSON.stringify(value);
          if (text && text !== "{}") return text.slice(0, 2000);
        } catch (_) {
          // Continue with another representation.
        }
      }
    }
    if (typeof data === "string" && data.trim()) return data.trim();
    if (data && typeof data === "object") {
      try {
        const text = JSON.stringify(data);
        if (text && text !== "{}") return text.slice(0, 2000);
      } catch (_) {
        // Fall through to ordinary error text.
      }
    }
    if (typeof error?.statusText === "string" && error.statusText.trim()) {
      return error.statusText.trim();
    }
    return String(error?.message || error || "Неизвестная ошибка ГИС ЖКХ.");
  }

  try {
    const angularRef = window.angular;
    if (!angularRef) {
      throw new Error(
        "Вкладка ГИС ЖКХ еще не готова. Дождитесь загрузки личного кабинета и повторите."
      );
    }

    let injector = null;
    const candidates = [
      document.querySelector("[ng-app]"),
      document.documentElement,
      document.body,
      document.querySelector(".ng-scope")
    ].filter(Boolean);

    for (const node of candidates) {
      try {
        injector = angularRef.element(node).injector();
      } catch (_) {
        injector = null;
      }
      if (injector) break;
    }

    if (!injector) {
      throw new Error(
        "Не удалось подключиться к интерфейсу ГИС ЖКХ. Обновите страницу личного кабинета."
      );
    }

    const $http = injector.get("$http");

    const STATE_FORMATION = "/workplanning/worklist/card/formation";
    const STATE_WORKPLANNING = "/workplanning";
    const STATE_FILE_REGISTRY = "/file/registry";

    // Background session renewal may overlap a long-running import.
    if (command?.type !== "keep_alive") window.__GIS_JKH_ASSISTANT_PROGRESS = null;

    function setProgress(progress) {
      window.__GIS_JKH_ASSISTANT_PROGRESS = progress || null;
    }

    function sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function assertOrganization() {
      const expected = command.payload?.organizationContext;
      if (!expected) return;
      const actual = (await getCurrentOrganization()).organization;
      const text = value => String(value || "").trim().toLowerCase();
      if (!expected.ogrn || text(expected.ogrn) !== text(actual.ogrn) ||
          (expected.organizationRootGuid &&
           text(expected.organizationRootGuid) !== text(actual.organizationRootGuid))) {
        throw new Error(
          "Организация во вкладке ГИС ЖКХ изменилась или не совпадает с выбранной УО. " +
          "Выберите нужный кабинет и повторите операцию."
        );
      }
    }

    async function request(method, url, data = undefined, stateGuid = STATE_WORKPLANNING, rawText = false) {
      if (method !== "GET") await assertOrganization();
      const config = {
        method,
        url,
        headers: {}
      };
      if (stateGuid) {
        config.headers["State-GUID"] = stateGuid;
      }
      if (data !== undefined) {
        config.data = data;
        config.headers["Content-Type"] = "application/json;charset=UTF-8";
      }
      // The fee-create endpoint returns a bare UUID with application/json.
      // Angular's default JSON transform would reject that successful response.
      if (rawText) config.transformResponse = [value => value];
      const response = await $http(config);
      return response.data;
    }

    async function requestBinary(method, url, stateGuid = null) {
      if (method !== "GET") await assertOrganization();
      const config = {
        method,
        url,
        responseType: "arraybuffer",
        headers: {}
      };
      if (stateGuid) {
        config.headers["State-GUID"] = stateGuid;
      }
      const response = await $http(config);
      return response.data;
    }

    async function getCurrentOrganization() {
      // Read the current authorized cabinet, not a locally selected house. The
      // information page uses this same GET without an organization parameter.
      let details;
      try {
        details = await request(
          "GET",
          "/ppa/api/rest/services/ppa/organizations/details",
          undefined,
          "/organization/information"
        );
      } catch (error) {
        if (error?.status === 401 || error?.status === 403) {
          throw new Error(
            "Войдите в личный кабинет ГИС ЖКХ и выберите управляющую организацию. Затем повторите загрузку."
          );
        }
        throw error;
      }
      const text = (value) => typeof value === "string" ? value.trim() : "";
      const name = text(details?.shortName) || text(details?.fullName);
      const ogrn = text(details?.ogrn);
      const organizationGuid = text(details?.guid);
      const organizationRootGuid = text(details?.registryOrganizationRootEntityGuid);
      if (!name || !/^\d{13}(\d{2})?$/.test(ogrn) ||
          !organizationGuid || !organizationRootGuid) {
        throw new Error(
          "ГИС ЖКХ не вернул сведения об организации. Откройте «Информация об организации» в личном кабинете, проверьте авторизацию и повторите загрузку."
        );
      }
      // Return only fields supported by the local organization model. Contacts,
      // personal names and all session information stay inside the browser.
      return {
        organization: {
          name,
          ogrn,
          kpp: text(details?.kpp) || null,
          organizationGuid,
          organizationRootGuid
        }
      };
    }

    function arrayBufferToBase64(buffer) {
      const bytes = new Uint8Array(buffer);
      const chunkSize = 0x8000;
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        const chunk = bytes.subarray(offset, offset + chunkSize);
        binary += String.fromCharCode(...chunk);
      }
      return btoa(binary);
    }

    function addressCriteria(houseInfo) {
      const address = houseInfo?.address || {};
      const criteria = {};

      const map = [
        ["regionRef", address.region?.aoGuid],
        ["areaRef", address.area?.aoGuid],
        ["cityRef", address.city?.aoGuid],
        ["settlementRef", address.settlement?.aoGuid],
        ["planningStructureElementRef", address.planningStructureElement?.aoGuid],
        ["streetRef", address.street?.aoGuid],
        ["houseRef", address.house?.houseGuid]
      ];

      for (const [key, code] of map) {
        if (code) criteria[key] = { guid: null, code };
      }
      return criteria;
    }

    async function getHouseInfo(houseGuid) {
      const data = await request(
        "GET",
        "/homemanagement/api/rest/services/houses/1/" + encodeURIComponent(houseGuid),
        undefined,
        STATE_WORKPLANNING
      );
      const item = Array.isArray(data) ? data[0] : data;
      if (!item) throw new Error("Дом не найден в ГИС ЖКХ.");

      const organizationGuid =
        item.managementOrganization?.registryOrganizationRootEntityGuid;
      if (!organizationGuid) {
        throw new Error("Не удалось определить управляющую организацию дома в ГИС ЖКХ.");
      }

      return { item, organizationGuid };
    }

    function integerOrZero(...values) {
      for (const value of values) {
        if (value === null || value === undefined || value === "") continue;
        const parsed = Number(value);
        if (Number.isFinite(parsed) && parsed >= 0) {
          return Math.trunc(parsed);
        }
      }
      return 0;
    }

    function normalizeManagedHouseRow(row) {
      const managementOrganization = row?.managementOrganization || {};
      const address = row?.address || {};
      const houseAddress = address?.house || {};
      const houseGuid = houseAddress?.houseGuid || null;
      return {
        address: address?.formattedAddress || "",
        houseObjectGuid: row?.guid || null,
        gisHouseGuid: houseGuid,
        fiasHouseGuid: houseGuid,
        gisUniqueHouseNumber: null,
        cadastralNumber: row?.cadastreNumber || null,
        residentialPremisesCount: integerOrZero(
          row?.residentialPremiseActualCount,
          row?.residentialPremiseConfirmedCount,
          row?.residentialPremiseCount
        ),
        nonResidentialPremisesCount: integerOrZero(
          row?.nonResidentialPremiseActualCount,
          row?.nonResidentialPremiseConfirmedCount,
          row?.nonResidentialPremiseCount
        ),
        organizationGuid: managementOrganization?.guid || null,
        organizationRootGuid:
          managementOrganization?.registryOrganizationRootEntityGuid || null,
        organizationName:
          managementOrganization?.shortName ||
          managementOrganization?.fullName ||
          null,
        organizationOgrn: managementOrganization?.ogrn || null,
        status: row?.status || null
      };
    }

    async function listManagedHouses(payload) {
      const pageSize = 10;
      let regionCode = String(payload?.regionCode || "").trim() || null;
      let organizationGuid = String(
        payload?.organizationGuid || ""
      ).trim() || null;
      let organizationRootGuid = String(
        payload?.organizationRootGuid || ""
      ).trim() || null;

      const seedHouseObjectGuid = String(
        payload?.seedHouseObjectGuid || ""
      ).trim();
      if (seedHouseObjectGuid) {
        const seed = await getHouseInfo(seedHouseObjectGuid);
        regionCode =
          regionCode ||
          seed?.item?.address?.region?.aoGuid ||
          seed?.item?.address?.region?.guid ||
          null;
        organizationGuid =
          organizationGuid ||
          seed?.item?.managementOrganization?.guid ||
          null;
        organizationRootGuid =
          organizationRootGuid ||
          seed?.item?.managementOrganization?.registryOrganizationRootEntityGuid ||
          seed?.organizationGuid ||
          null;
      }

      const body = {
        regionCode,
        statuses: ["APPROVED"],
        houseConditionRefList: null,
        hasIncorrectObjects: null,
        needRepair: null,
        fiasHouseCodeList: null,
        calcCount: true,
        houseManagementTypeRefList: null,
        houseTypeRefList: null,
        attrFundCodes: null,
        oktmo: null,
        managementOrganizationGuid: organizationGuid,
        localGovernmentOrganizationGuid: null,
        regionProperty: null,
        municipalProperty: null,
        hostelTypeCodes: null
      };

      const items = [];
      const seen = new Set();
      let total = null;

      for (let page = 1; page <= 1000; page += 1) {
        const response = await request(
          "POST",
          "/homemanagement/api/rest/services/houses/search;page=" +
            page +
            ";itemsPerPage=" +
            pageSize,
          body,
          STATE_WORKPLANNING
        );
        const rows = Array.isArray(response?.items) ? response.items : [];
        const responseTotal = Number(response?.total);
        if (Number.isFinite(responseTotal) && responseTotal >= 0) {
          total = responseTotal;
        }

        for (const row of rows) {
          const normalized = normalizeManagedHouseRow(row);
          const key = String(
            normalized.houseObjectGuid ||
            normalized.gisHouseGuid ||
            normalized.address
          ).trim();
          if (!key || seen.has(key)) continue;
          seen.add(key);
          items.push(normalized);
          organizationGuid =
            organizationGuid || normalized.organizationGuid || null;
          organizationRootGuid =
            organizationRootGuid || normalized.organizationRootGuid || null;
        }

        if (!rows.length) break;
        if (total !== null && items.length >= total) break;
        if (rows.length < pageSize && total === null) break;
      }

      return {
        pageSize,
        total: total === null ? items.length : total,
        items,
        organizationGuid,
        organizationRootGuid
      };
    }

    function ruDateSortKey(value) {
      const text = String(value || "").trim();
      const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
      if (!match) return 0;
      return Number(match[3] + match[2] + match[1]);
    }

    function nextDayRuDate(value) {
      const text = String(value || "").trim();
      const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
      if (!match) return text || null;
      const date = new Date(
        Date.UTC(Number(match[3]), Number(match[2]) - 1, Number(match[1]))
      );
      date.setUTCDate(date.getUTCDate() + 1);
      const day = String(date.getUTCDate()).padStart(2, "0");
      const month = String(date.getUTCMonth() + 1).padStart(2, "0");
      return day + "." + month + "." + String(date.getUTCFullYear());
    }

    function houseIdentityValues(value) {
      const result = new Set();
      const add = (candidate) => {
        const text = String(candidate || "").trim().toLowerCase();
        if (text) result.add(text);
      };
      add(value?.guid);
      add(value?.rootGuid);
      add(value?.houseHMGuid);
      add(value?.hmHouseShortInfoDto?.houseHmGuid);
      add(value?.fiasHouseAddress?.house?.guid);
      add(value?.fiasHouseAddress?.house?.code);
      add(value?.hmHouseShortInfoDto?.fiasHouse?.guid);
      add(value?.hmHouseShortInfoDto?.fiasHouse?.code);
      return result;
    }

    function publicManagementRowSort(left, right) {
      const updateDiff =
        Number(right?.lastUpdateDate || 0) -
        Number(left?.lastUpdateDate || 0);
      if (updateDiff) return updateDiff;

      const startDiff =
        ruDateSortKey(right?.managedDateStart) -
        ruDateSortKey(left?.managedDateStart);
      if (startDiff) return startDiff;

      const leftCurrent = left?.managedDateEnd ? 0 : 1;
      const rightCurrent = right?.managedDateEnd ? 0 : 1;
      return rightCurrent - leftCurrent;
    }

    async function fetchPublicManagementRows(organizationRootGuid) {
      const result = [];
      const seenRemoteRows = new Set();
      for (let pageIndex = 1; pageIndex <= 100; pageIndex += 1) {
        const body = {
          period: { from: null, to: null },
          organizationRootGuid,
          archived: false,
          managementPeriod: "ALL_HOUSE",
          fullAddressSearchCriteria: {},
          pageIndex,
          elementsPerPage: 100
        };
        const response = await request(
          "POST",
          "/licenses/api/rest/services/public/house/search-by-org",
          body,
          STATE_WORKPLANNING
        );
        const rows = Array.isArray(response) ? response : [];
        if (!rows.length) break;

        let newRows = 0;
        for (const row of rows) {
          const rowKey = String(
            row?.guid ||
            [
              row?.hmHouseShortInfoDto?.houseHmGuid,
              row?.managedDateStart,
              row?.managedDateEnd,
              row?.agreementNumber
            ].join("|")
          );
          if (seenRemoteRows.has(rowKey)) continue;
          seenRemoteRows.add(rowKey);
          newRows += 1;
          result.push(row);
        }
        if (!newRows) break;
      }
      result.sort(publicManagementRowSort);
      return result;
    }

    function normalizePublicManagementRow(row, organizationRootGuid) {
      const agreement = row?.agreement || {};
      return {
        identities: Array.from(houseIdentityValues(row)),
        managementStartDate: row?.managedDateStart || null,
        managementEndDate: row?.managedDateEnd
          ? nextDayRuDate(row.managedDateEnd)
          : null,
        publicManagedDateEnd: row?.managedDateEnd || null,
        agreementNumber:
          row?.agreementNumber || agreement?.number || null,
        agreementDate:
          row?.documentDate ||
          agreement?.signDateText ||
          agreement?.signDate ||
          null,
        organizationGuid:
          row?.orgGuid ||
          row?.managementOrg?.guid ||
          null,
        organizationRootGuid:
          row?.orgRootGuid ||
          row?.managementOrg?.rootEntityGuid ||
          organizationRootGuid,
        lastUpdateDate: row?.lastUpdateDate || null,
        source: "gis_public"
      };
    }

    async function listPublicManagementInfo(payload) {
      let organizationRootGuid = String(
        payload?.organizationRootGuid || ""
      ).trim();
      if (!organizationRootGuid) {
        const seedHouseObjectGuid = String(
          payload?.seedHouseObjectGuid || ""
        ).trim();
        if (!seedHouseObjectGuid) {
          throw new Error(
            "Не удалось определить организацию: не передан дом для получения GUID."
          );
        }
        const houseContext = await getHouseInfo(seedHouseObjectGuid);
        organizationRootGuid = String(
          houseContext?.organizationGuid || ""
        ).trim();
      }
      if (!organizationRootGuid) {
        throw new Error("Не удалось определить организацию для публичного реестра.");
      }
      const rows = await fetchPublicManagementRows(organizationRootGuid);
      return {
        organizationRootGuid,
        items: rows.map((row) =>
          normalizePublicManagementRow(row, organizationRootGuid)
        )
      };
    }

    async function getPublicManagementInfo(payload) {
      const houseContext = await getHouseInfo(payload?.houseObjectGuid);
      const organizationRootGuid = String(
        houseContext?.organizationGuid || ""
      ).trim();
      if (!organizationRootGuid) {
        throw new Error("Не удалось определить организацию для публичного реестра.");
      }

      const localIds = new Set();
      const addLocal = (candidate) => {
        const text = String(candidate || "").trim().toLowerCase();
        if (text) localIds.add(text);
      };
      addLocal(payload?.houseObjectGuid);
      addLocal(payload?.gisHouseGuid);
      addLocal(payload?.fiasHouseGuid);
      addLocal(houseContext?.item?.guid);
      addLocal(houseContext?.item?.rootGuid);
      addLocal(houseContext?.item?.address?.house?.houseGuid);
      addLocal(houseContext?.item?.address?.house?.aoGuid);

      const rows = await fetchPublicManagementRows(organizationRootGuid);
      const candidates = rows.filter((row) => {
        const ids = houseIdentityValues(row);
        return Array.from(localIds).some((id) => ids.has(id));
      });

      if (!candidates.length) {
        throw new Error(
          "Дом не найден в открытых сведениях о деятельности по управлению."
        );
      }

      return normalizePublicManagementRow(
        candidates[0],
        organizationRootGuid
      );
    }

    function periodMatches(item, payload) {
      return (
        item?.houseGuid === payload.houseGuid &&
        Number(item?.period?.from?.month) === Number(payload.startMonth) &&
        Number(item?.period?.from?.year) === Number(payload.startYear) &&
        Number(item?.period?.to?.month) === Number(payload.endMonth) &&
        Number(item?.period?.to?.year) === Number(payload.endYear)
      );
    }

    async function searchExactWorkList(payload, statuses = null) {
      const house = await getHouseInfo(payload.houseGuid);
      const body = {
        period: { from: null, to: null },
        organizationGuid: house.organizationGuid,
        addressSearchCriteria: addressCriteria(house.item)
      };
      if (statuses?.length) body.statuses = statuses;

      const data = await request(
        "POST",
        "/workplanning/api/rest/services/search/worklists;pageIndex=1;elementsPerPage=50",
        body,
        STATE_WORKPLANNING
      );

      const items = Array.isArray(data?.items) ? data.items : [];
      const match = items.find((item) => periodMatches(item, payload)) || null;
      return match;
    }

    function statusResult(item, extras = {}) {
      if (!item) {
        return Object.assign(
          {
            found: false,
            gisStatus: "NOT_FOUND",
            workListGuid: null
          },
          extras
        );
      }

      return Object.assign(
        {
          found: true,
          gisStatus: item.status || null,
          workListGuid: item.guid || null,
          wasApproved: Boolean(item.wasApproved),
          lastUpdateDate: item.lastUpdateDate || null,
          totalCost: item.summary?.totalCost || null,
          monthsCount: item.summary?.monthsCount || null,
          worksCount: item.summary?.worksCount || null
        },
        extras
      );
    }

    function normalizeWorkListItemForImport(item) {
      return {
        sortIndex: item?.sortIndex ?? null,
        workGuid: item?.work?.guid || null,
        workCode: item?.work?.code || null,
        workName: item?.work?.workName || "",
        unitName:
          item?.unit ||
          item?.okei?.nationalSymbolName ||
          item?.work?.dimensionsUnit?.dimensionsUnitString ||
          null,
        price: item?.price ?? null,
        volume: item?.amount ?? null,
        quantity: item?.count ?? null,
        cost: item?.totalCost ?? null
      };
    }

    function normalizeWorkListForImport(item) {
      return {
        guid: item?.guid || null,
        houseGuid: item?.houseGuid || null,
        status: item?.status || null,
        wasApproved: Boolean(item?.wasApproved),
        lastUpdateDate: item?.lastUpdateDate || null,
        period: {
          from: {
            month: item?.period?.from?.month ?? null,
            year: item?.period?.from?.year ?? null
          },
          to: {
            month: item?.period?.to?.month ?? null,
            year: item?.period?.to?.year ?? null
          }
        },
        summary: {
          totalSquare: item?.summary?.totalSquare ?? null,
          monthsCount: item?.summary?.monthsCount ?? null,
          worksCount: item?.summary?.worksCount ?? null,
          totalCost: item?.summary?.totalCost ?? null
        },
        items: (Array.isArray(item?.workListItems) ? item.workListItems : []).map(
          normalizeWorkListItemForImport
        )
      };
    }

    async function listOrganizationWorkLists(payload) {
      const requestedHouseObjectGuid = String(
        payload?.houseObjectGuid || ""
      ).trim();
      const seedHouseObjectGuid = String(
        requestedHouseObjectGuid || payload?.seedHouseObjectGuid || ""
      ).trim();
      if (!seedHouseObjectGuid) {
        throw new Error(
          "Для загрузки перечней нужен хотя бы один дом с GUID объекта жилищного фонда."
        );
      }

      const house = await getHouseInfo(seedHouseObjectGuid);
      const organizationGuid = String(house?.organizationGuid || "").trim();
      if (!organizationGuid) {
        throw new Error(
          "Не удалось определить организацию для загрузки перечней из ГИС ЖКХ."
        );
      }

      const seedCriteria = addressCriteria(house?.item);
      const addressSearchCriteria = {};
      if (requestedHouseObjectGuid) {
        Object.assign(addressSearchCriteria, seedCriteria);
      } else if (seedCriteria.regionRef) {
        addressSearchCriteria.regionRef = seedCriteria.regionRef;
      }

      const pageSize = 10;
      const seen = new Set();
      const items = [];
      let pages = 0;
      setProgress({ phase: "receiving_work_lists", received: 0 });

      // The work-list endpoint observed in the GIS ЖКХ UI reports "total" as the
      // number of rows on the current page, not as a reliable grand total.
      // Therefore pagination continues until a short/empty page. Repeated GUIDs
      // are also used as a safety stop so a server-side paging regression cannot
      // cause an endless loop.
      for (let page = 1; page <= 500; page += 1) {
        const data = await request(
          "POST",
          "/workplanning/api/rest/services/search/worklists;pageIndex=" +
            page +
            ";elementsPerPage=" +
            pageSize,
          {
            period: { from: null, to: null },
            organizationGuid,
            addressSearchCriteria
          },
          STATE_WORKPLANNING
        );

        const pageItems = Array.isArray(data?.items) ? data.items : [];
        pages = page;
        if (!pageItems.length) break;

        let added = 0;
        for (const item of pageItems) {
          const guid = String(item?.guid || "").trim();
          if (!guid || seen.has(guid)) continue;
          seen.add(guid);
          items.push(normalizeWorkListForImport(item));
          added += 1;
        }

        setProgress({
          phase: "receiving_work_lists",
          received: items.length
        });

        if (pageItems.length < pageSize || added === 0) break;
      }

      return {
        organizationGuid,
        pages,
        scope: requestedHouseObjectGuid ? "house" : "organization",
        houseObjectGuid: requestedHouseObjectGuid || null,
        items
      };
    }

    async function downloadWorkListExportTemplate(payload) {
      const houseGuid = String(payload?.houseObjectGuid || "").trim();
      if (!houseGuid) {
        throw new Error("Для выгрузки шаблона нужен GUID объекта жилищного фонда.");
      }

      const year = String(payload?.year || new Date().getFullYear());
      const month = String(payload?.month || 1).padStart(2, "0");

      setProgress({ phase: "template_export", status: "requesting" });
      const exportResult = await request(
        "POST",
        "/ext-bus-organization-ie-service/export",
        {
          criteria: {
            houseGuid,
            yearFrom: year,
            monthFrom: month,
            yearTo: year,
            monthTo: month
          },
          operationType: "EXPORT_WORK_LIST_V2"
        },
        STATE_FORMATION
      );

      const jobGuid = String(exportResult?.guid || "").trim();
      if (!jobGuid) {
        throw new Error(
          exportResult?.message ||
            "ГИС ЖКХ не вернул идентификатор задания выгрузки шаблона."
        );
      }

      const searchBody = {
        createDateFrom: null,
        createDateTo: null,
        startProcessingDateFrom: null,
        startProcessingDateTo: null,
        processDateFrom: null,
        processDateTo: null
      };

      for (let attempt = 1; attempt <= 80; attempt += 1) {
        setProgress({
          phase: "template_export",
          status: "processing",
          attempt
        });

        const registry = await request(
          "POST",
          "/monitoring/api/rest/services/files/search;page=1;itemsPerPage=10",
          searchBody,
          STATE_FILE_REGISTRY
        );
        const item = (Array.isArray(registry?.items) ? registry.items : []).find(
          (row) => String(row?.guid || "") === jobGuid
        );

        if (item?.status === "PROCESSED" && item?.downloadAvailable) {
          const contentGuid = String(item?.resultContentGuid || "").trim();
          if (!contentGuid) {
            throw new Error(
              "ГИС ЖКХ завершил выгрузку, но не вернул GUID готового файла."
            );
          }

          setProgress({ phase: "template_export", status: "downloading" });
          const buffer = await requestBinary(
            "GET",
            "/filestore/downloadServlet?uid=" + encodeURIComponent(contentGuid)
          );
          return {
            jobGuid,
            fileName: item?.fileName || "Экспорт перечня работ.xlsx",
            fileBase64: arrayBufferToBase64(buffer)
          };
        }

        if (
          item &&
          item.status &&
          ![
            "NEW",
            "CREATED",
            "PROCESSING",
            "IN_PROCESS",
            "NOT_PROCESSED"
          ].includes(String(item.status).toUpperCase()) &&
          item.status !== "PROCESSED"
        ) {
          throw new Error(
            item?.errorDescription ||
              "ГИС ЖКХ завершил выгрузку шаблона с ошибкой: " +
                String(item.status)
          );
        }

        await sleep(750);
      }

      throw new Error(
        "ГИС ЖКХ не успел подготовить шаблон перечня. Повторите операцию позже."
      );
    }

    async function uploadFile(payload) {
      const bytes = Uint8Array.from(atob(payload.fileBase64), (char) =>
        char.charCodeAt(0)
      );
      const blob = new Blob([bytes], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      });
      const size = blob.size;
      const filename = payload.fileName;
      const identifier = size + "-" + filename.replace(/[^\w-]/g, "");

      const form = new FormData();
      const values = {
        context: "homemanagement",
        description: filename,
        fileName: filename,
        chunkNumber: "1",
        totalChunks: "1",
        fileSize: String(size),
        flowChunkNumber: "1",
        flowChunkSize: "5242880",
        flowCurrentChunkSize: String(size),
        flowTotalSize: String(size),
        flowIdentifier: identifier,
        flowFilename: filename,
        flowRelativePath: filename,
        flowTotalChunks: "1"
      };

      for (const [key, value] of Object.entries(values)) {
        form.append(key, value);
      }
      form.append("file", blob, filename);

      await assertOrganization();
      const response = await fetch(
        "/filestore/api/rest/services/filestore/uploadFile",
        {
          method: "POST",
          body: form,
          credentials: "include"
        }
      );
      if (!response.ok) {
        throw new Error("Не удалось загрузить XLSX в файловое хранилище ГИС ЖКХ.");
      }

      const uploaded = await response.json();
      if (!uploaded?.guid) {
        throw new Error("ГИС ЖКХ не вернул GUID загруженного файла.");
      }
      return uploaded.guid;
    }

    async function waitFileReady(fileGuid) {
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const data = await request(
          "POST",
          "/filestore/api/rest/services/filestore/fileDetails",
          { fileUids: [fileGuid] },
          STATE_FORMATION
        );
        const item = Array.isArray(data) ? data[0] : null;
        if (item?.infected) {
          throw new Error("ГИС ЖКХ сообщил, что загруженный файл заражен.");
        }
        if (item?.virusChecked && item?.downloadAvailable) {
          return item;
        }
        await sleep(750);
      }
      throw new Error("ГИС ЖКХ слишком долго проверяет загруженный файл.");
    }

    async function importFile(fileGuid) {
      const response = await request(
        "POST",
        "/ext-bus-organization-ie-service/import",
        { fileGuid },
        STATE_FORMATION
      );
      if (response?.status && response.status !== "OK") {
        throw new Error(response.message || "ГИС ЖКХ отклонил импорт файла.");
      }
      return response?.guid || null;
    }

    function compactReference(value) {
      if (!value) return null;
      return {
        code: value.code ?? null,
        guid: value.guid ?? null
      };
    }

    function approvalPayloadFromDraft(draft) {
      // The search API returns a rich WorkList object. The approve endpoint accepts
      // WorkListAlter and rejects extra fields such as houseTypeCode, summary,
      // organizationName and the expanded directory objects. This shape mirrors
      // the payload sent by the GIS ЖКХ web interface.
      return {
        guid: draft.guid ?? null,
        houseGuid: draft.houseGuid ?? null,
        period: draft.period ?? null,
        attachmentList: Array.isArray(draft.attachmentList)
          ? draft.attachmentList
          : [],
        lastUpdateDate: draft.lastUpdateDate ?? null,
        createDate: draft.createDate ?? null,
        workListItems: (draft.workListItems || []).map((item) => ({
          work: compactReference(item.work),
          count: item.count ?? null,
          price: item.price ?? null,
          amount: item.amount ?? null,
          totalCost: item.totalCost ?? null,
          costPerMeter: item.costPerMeter ?? null,
          sortIndex: item.sortIndex ?? null,
          // The GIS web form sends an empty display unit here and the OKEI
          // reference separately.
          unit: "",
          workType: compactReference(item.workType),
          okei: compactReference(item.okei),
          isPlannedWork: item.isPlannedWork ?? null
        }))
      };
    }

    async function getWorkListDetailAny(workListGuid) {
      const guid = String(workListGuid || "").trim();
      if (!guid) {
        throw new Error("Не передан GUID перечня ГИС ЖКХ.");
      }

      const data = await request(
        "GET",
        "/workplanning/api/rest/services/search/worklist/" + encodeURIComponent(guid),
        undefined,
        STATE_WORKPLANNING
      );
      if (!data?.guid) {
        throw new Error("Перечень не найден в ГИС ЖКХ.");
      }
      return data;
    }

    async function getWorkListDetail(workListGuid) {
      const data = await getWorkListDetailAny(workListGuid);
      if (data.status !== "APPROVED") {
        throw new Error(
          "Перечень больше не находится в статусе «Размещен» в ГИС ЖКХ."
        );
      }
      return data;
    }

    function nullableString(value) {
      const text = String(value ?? "").trim();
      return text ? text : null;
    }

    async function buildEditableWorkListPayload(localPayload, remote) {
      const localItems = Array.isArray(localPayload?.items)
        ? localPayload.items
        : [];
      if (!localItems.length) {
        throw new Error(
          "В перечне нет работ. Добавьте актуальные работы перед сохранением в ГИС ЖКХ."
        );
      }

      const workListItems = [];
      for (let index = 0; index < localItems.length; index += 1) {
        const local = localItems[index] || {};
        const work = await getOrganizationWork(local.workCode, local.workGuid);
        if (String(work?.status || "").toUpperCase() !== "ACTIVE") {
          throw new Error(
            "Работа «" +
              String(local.workName || local.workCode || "без названия") +
              "» отсутствует в актуальном справочнике организации. " +
              "Удалите её или замените актуальной работой."
          );
        }

        const rawCount = nullableString(local.quantity);
        const count = rawCount === null ? null : Number(rawCount);
        if (
          count !== null &&
          (!Number.isFinite(count) || count < 0)
        ) {
          throw new Error(
            "Некорректное количество у работы «" +
              String(local.workName || local.workCode || "") +
              "»."
          );
        }

        workListItems.push({
          work: compactReference(work),
          count,
          price: nullableString(local.price),
          amount: nullableString(local.volume),
          totalCost: nullableString(local.cost),
          costPerMeter: null,
          sortIndex: index + 1,
          unit: "",
          workType: compactReference(work?.serviceType),
          okei: compactReference(
            work?.dimensionsUnit?.dimensionsUnitOkei || null
          ),
          isPlannedWork: null
        });
      }

      const period =
        localPayload?.startYear && localPayload?.startMonth &&
        localPayload?.endYear && localPayload?.endMonth
          ? {
              from: {
                year: Number(localPayload.startYear),
                month: Number(localPayload.startMonth)
              },
              to: {
                year: Number(localPayload.endYear),
                month: Number(localPayload.endMonth)
              },
              yearsCount: remote?.period?.yearsCount ?? 0
            }
          : remote.period;

      return {
        guid: remote.guid,
        houseGuid: remote.houseGuid,
        period,
        attachmentList: Array.isArray(remote.attachmentList)
          ? remote.attachmentList
          : [],
        lastUpdateDate: remote.lastUpdateDate || null,
        createDate: remote.createDate || null,
        workListItems
      };
    }

    async function saveEditableWorkList(payload) {
      const remote = await getWorkListDetailAny(payload.workListGuid);
      if (!["CANCELLED", "DRAFT"].includes(String(remote.status || ""))) {
        throw new Error(
          "Изменять состав через программу можно только у отменённого перечня " +
            "или проекта ГИС ЖКХ."
        );
      }

      const workList = await buildEditableWorkListPayload(payload, remote);
      await request(
        "POST",
        "/workplanning/api/rest/services/save/worklist",
        { workList },
        STATE_WORKPLANNING
      );

      await sleep(600);
      const fresh = await getWorkListDetailAny(remote.guid);
      return statusResult(fresh);
    }

    async function cancelWorkList(payload) {
      const remote = await getWorkListDetailAny(payload.workListGuid);
      if (String(remote.status || "") === "CANCELLED") {
        return statusResult(remote);
      }
      if (String(remote.status || "") !== "APPROVED") {
        throw new Error(
          "Отменить можно только перечень в статусе «Размещен»."
        );
      }

      await request(
        "PUT",
        "/workplanning/api/rest/services/cancel/worklist",
        { workListGuid: remote.guid },
        STATE_WORKPLANNING
      );
      await sleep(600);
      const fresh = await getWorkListDetailAny(remote.guid);
      return statusResult(fresh, { gisStatus: fresh.status || "CANCELLED" });
    }

    async function searchWorkPlan(workListGuid, year) {
      const data = await request(
        "POST",
        "/workplanning/api/rest/services/search/workplans",
        {
          workListGuid: String(workListGuid),
          year: Number(year)
        },
        STATE_WORKPLANNING
      );
      return data?.workPlan || null;
    }

    function gisDateToIso(value) {
      const text = String(value || "").trim();
      if (!text) return null;
      const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(text);
      if (iso) return iso[1] + "-" + iso[2] + "-" + iso[3];
      const ru = /^(\d{2})\.(\d{2})\.(\d{4})/.exec(text);
      if (ru) return ru[3] + "-" + ru[2] + "-" + ru[1];
      return null;
    }

    function normalizeWorkPlanForImport(plan) {
      if (!plan?.guid) return { found: false };
      return {
        found: true,
        workPlanGuid: plan.guid,
        workListGuid: plan.workListGuid || null,
        year: Number(plan.year || 0),
        gisStatus: "SAVED",
        lastUpdateDate: plan.lastUpdateDate || null,
        items: (Array.isArray(plan.workPlanItems) ? plan.workPlanItems : []).map(
          (item, index) => ({
            guid: item?.guid || null,
            sortIndex: item?.sortIndex ?? index + 1,
            workGuid: item?.work?.guid || null,
            workCode: item?.work?.code || null,
            workName:
              item?.work?.workName ||
              item?.workName ||
              item?.work?.name ||
              "",
            listQuantity: (Array.isArray(item?.monthlyWorks) ? item.monthlyWorks : [])
              .reduce((total, month) => total + Number(month?.count || 0), 0),
            monthlyWorks: (
              Array.isArray(item?.monthlyWorks) ? item.monthlyWorks : []
            ).map((month) => ({
              month: Number(month?.monthYear?.month || 0),
              count: Number(month?.count || 0),
              dates: (Array.isArray(month?.dates) ? month.dates : [])
                .map((row) => gisDateToIso(row?.date ?? row))
                .filter(Boolean)
            }))
          })
        )
      };
    }

    function planDateForGis(value) {
      const text = String(value || "").trim();
      const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
      if (!match) {
        throw new Error("Некорректная локальная дата плана: " + text);
      }
      return match[3] + "." + match[2] + "." + match[1] + " 00:00:00:000";
    }

    function findWorkListItem(localItem, remoteItems) {
      const workGuid = String(localItem?.workGuid || "").trim();
      const workCode = String(localItem?.workCode || "").trim();
      const sortIndex = Number(localItem?.sortIndex);

      let matches = [];
      if (workGuid) {
        matches = remoteItems.filter(
          (item) => String(item?.work?.guid || "") === workGuid
        );
      }
      if (!matches.length && workCode) {
        matches = remoteItems.filter(
          (item) => String(item?.work?.code || "") === workCode
        );
      }
      if (matches.length > 1 && Number.isFinite(sortIndex)) {
        const byPosition = matches.find(
          (item) => Number(item?.sortIndex) === sortIndex
        );
        if (byPosition) return byPosition;
      }
      if (matches.length === 1) return matches[0];

      if (Number.isFinite(sortIndex)) {
        const byPosition = remoteItems.find(
          (item) => Number(item?.sortIndex) === sortIndex
        );
        if (
          byPosition &&
          (!workGuid || String(byPosition?.work?.guid || "") === workGuid) &&
          (!workCode || String(byPosition?.work?.code || "") === workCode)
        ) {
          return byPosition;
        }
      }

      throw new Error(
        "Не удалось сопоставить строку локального плана с размещенным перечнем " +
          "в ГИС ЖКХ. Проверьте состав перечня и повторите."
      );
    }

    function findExistingPlanItem(sourceItem, existingPlan) {
      const items = Array.isArray(existingPlan?.workPlanItems)
        ? existingPlan.workPlanItems
        : [];
      const workGuid = String(sourceItem?.work?.guid || "");
      const sortIndex = Number(sourceItem?.sortIndex);

      const byGuid = items.filter(
        (item) => String(item?.work?.guid || "") === workGuid
      );
      if (byGuid.length === 1) return byGuid[0];
      if (byGuid.length > 1 && Number.isFinite(sortIndex)) {
        return (
          byGuid.find((item) => Number(item?.sortIndex) === sortIndex) || null
        );
      }
      return null;
    }

    function saveWorkPlanPayload(localPayload, workList, existingPlan) {
      const localItems = Array.isArray(localPayload?.items)
        ? localPayload.items
        : [];
      const remoteItems = Array.isArray(workList?.workListItems)
        ? workList.workListItems
        : [];
      if (!remoteItems.length) {
        throw new Error("В размещенном перечне ГИС ЖКХ нет работ и услуг.");
      }

      const usedRemoteItemGuids = new Set();
      const year = Number(localPayload.year);
      if (!Number.isInteger(year)) {
        throw new Error("Некорректный год плана.");
      }

      const workPlanItems = localItems.map((localItem, index) => {
        const sourceItem = findWorkListItem(localItem, remoteItems);
        if (sourceItem?.guid) {
          if (usedRemoteItemGuids.has(sourceItem.guid)) {
            throw new Error(
              "Несколько строк локального плана сопоставились с одной строкой ГИС ЖКХ."
            );
          }
          usedRemoteItemGuids.add(sourceItem.guid);
        }

        const existingItem = findExistingPlanItem(sourceItem, existingPlan);
        const monthlyWorks = (localItem?.monthlyWorks || []).map((month) => {
          const monthNumber = Number(month?.month);
          const count = Number(month?.count);
          if (
            !Number.isInteger(monthNumber) ||
            monthNumber < 1 ||
            monthNumber > 12 ||
            !Number.isInteger(count) ||
            count < 0
          ) {
            throw new Error("Некорректное количество или месяц локального плана.");
          }

          const dates = (Array.isArray(month?.dates) ? month.dates : []).map(
            (value) => ({ date: planDateForGis(value) })
          );
          if (dates.length > count) {
            throw new Error(
              "Количество в месяце меньше числа выбранных конкретных дат."
            );
          }

          return {
            count,
            dates,
            monthYear: {
              month: monthNumber,
              year
            }
          };
        });

        const result = {
          work: compactReference(sourceItem.work),
          workType: compactReference(sourceItem.workType),
          monthlyWorks,
          sortIndex: sourceItem.sortIndex ?? localItem.sortIndex ?? index + 1
        };
        if (existingItem?.guid) {
          result.guid = existingItem.guid;
        }
        return result;
      });

      if (usedRemoteItemGuids.size !== remoteItems.length) {
        throw new Error(
          "Состав локального плана отличается от размещенного перечня в ГИС ЖКХ. " +
            "Проверьте статус перечня и создайте план заново."
        );
      }

      const workPlan = {
        workListGuid: workList.guid,
        year,
        workPlanItems
      };
      if (existingPlan?.guid) {
        workPlan.guid = existingPlan.guid;
        if (existingPlan.lastUpdateDate) {
          workPlan.lastUpdateDate = existingPlan.lastUpdateDate;
        }
      }
      return { workPlan };
    }

    async function getWorkplanningHouseByFias(fiasHouseCode) {
      const fias = String(fiasHouseCode || "").trim();
      if (!fias) {
        throw new Error("Не передан ФИАС GUID дома.");
      }
      const data = await request(
        "GET",
        "/workplanning/api/rest/services/find/house/by/fias?fiasHouseCode=" +
          encodeURIComponent(fias),
        undefined,
        STATE_WORKPLANNING
      );
      if (!data?.guid) {
        throw new Error("Дом не найден в модуле планирования ГИС ЖКХ.");
      }
      return data;
    }

    async function findReportingPeriod(payload) {
      const house = await getWorkplanningHouseByFias(payload.fiasHouseCode);
      const data = await request(
        "POST",
        "/workplanning/api/rest/services/search/reportingperiods",
        { houseGuid: house.guid },
        STATE_WORKPLANNING
      );
      const periods = Array.isArray(data?.periods) ? data.periods : [];
      const result =
        periods.find(
          (period) =>
            String(period?.workListGuid || "") === String(payload.workListGuid || "") &&
            Number(period?.period?.year) === Number(payload.year) &&
            Number(period?.period?.month) === Number(payload.month) &&
            !period?.archived
        ) || null;
      if (!result?.guid) {
        throw new Error(
          "В ГИС ЖКХ не найден период фиксации для выбранного перечня, месяца и года."
        );
      }
      return result;
    }

    async function searchFixedWorks(reportingPeriodGuid) {
      return request(
        "POST",
        "/workplanning/api/rest/services/search/fixedworks",
        { workFixationPeriodGuid: reportingPeriodGuid },
        STATE_WORKPLANNING
      );
    }

    function fixedWorkUnitName(item) {
      const work = item?.work || {};
      return (
        item?.unitName ||
        work?.dimensionsUnit?.dimensionsUnitOkei?.nationalSymbolName ||
        work?.dimensionsUnit?.dimensionsUnitString ||
        null
      );
    }

    function normalizeFixedWorkForImport(item, index) {
      const monthly = item?.monthlyFixedWork || {};
      return {
        guid: item?.guid || null,
        position: index + 1,
        workPlanItemGuid: item?.workPlanItemGuid || null,
        workGuid: item?.work?.guid || null,
        workCode: item?.work?.code || null,
        workName:
          item?.workName ||
          item?.work?.workName ||
          item?.work?.name ||
          "",
        workTypeGuid: item?.workType?.guid || null,
        workTypeCode: item?.workType?.code || null,
        workTypeName:
          item?.workTypeName ||
          item?.workType?.serviceName ||
          item?.workType?.name ||
          null,
        unitName: fixedWorkUnitName(item),
        plannedPrice: item?.plannedPrice ?? null,
        plannedAmount: item?.plannedAmount ?? null,
        plannedCount:
          item?.monthlyPlannedWork?.count === null ||
          item?.monthlyPlannedWork?.count === undefined
            ? null
            : Number(item.monthlyPlannedWork.count),
        plannedTotalCost: item?.plannedTotalCost ?? null,
        factPrice: item?.factPrice ?? null,
        factAmount: item?.factAmount ?? null,
        factCount:
          monthly?.count === null || monthly?.count === undefined
            ? null
            : Number(monthly.count),
        factTotalCost: item?.factTotalCost ?? null,
        comment: item?.comment || null
      };
    }

    async function loadWorkFixation(payload) {
      let reportingPeriod = null;
      try {
        reportingPeriod = await findReportingPeriod(payload);
      } catch (_) {
        return { found: false };
      }

      const remote = await searchFixedWorks(reportingPeriod.guid);
      const planned = Array.isArray(remote?.plannedWorkList)
        ? remote.plannedWorkList
        : [];
      const unplanned = Array.isArray(remote?.unplannedWorkList)
        ? remote.unplannedWorkList
        : [];
      const found =
        planned.some((item) => Boolean(item?.guid)) ||
        unplanned.some((item) => Boolean(item?.guid));

      if (!found) {
        return {
          found: false,
          reportingPeriodGuid: reportingPeriod.guid
        };
      }

      return {
        found: true,
        reportingPeriodGuid: reportingPeriod.guid,
        gisStatus: "SAVED",
        plannedItems: planned.map(normalizeFixedWorkForImport),
        unplannedItems: unplanned.map(normalizeFixedWorkForImport)
      };
    }

    function fixationLocalItemMatch(localItem, remoteItems, usedIndexes) {
      const workGuid = String(localItem?.workGuid || "").trim();
      const workCode = String(localItem?.workCode || "").trim();

      function candidates(predicate) {
        return remoteItems
          .map((item, index) => ({ item, index }))
          .filter(({ index }) => !usedIndexes.has(index))
          .filter(({ item }) => predicate(item));
      }

      let matches = [];
      if (workGuid) {
        matches = candidates(
          (item) => String(item?.work?.guid || "") === workGuid
        );
      }
      if (!matches.length && workCode) {
        matches = candidates(
          (item) => String(item?.work?.code || "") === workCode
        );
      }
      if (matches.length === 1) {
        usedIndexes.add(matches[0].index);
        return matches[0];
      }

      const position = Number(localItem?.sortIndex);
      const index = position - 1;
      if (
        Number.isInteger(index) &&
        index >= 0 &&
        index < remoteItems.length &&
        !usedIndexes.has(index)
      ) {
        const item = remoteItems[index];
        const guidMatches =
          !workGuid || String(item?.work?.guid || "") === workGuid;
        const codeMatches =
          !workCode || String(item?.work?.code || "") === workCode;
        if (guidMatches && codeMatches) {
          usedIndexes.add(index);
          return { item, index };
        }
      }

      throw new Error(
        "Не удалось сопоставить локальную строку фиксации с плановой работой в ГИС ЖКХ."
      );
    }

    function fixationNumberForGis(value, label) {
      if (value === null || value === undefined || String(value).trim() === "") {
        return null;
      }
      const text = String(value)
        .replace(/\u00a0/g, "")
        .replace(/\s+/g, "")
        .replace(",", ".");
      const number = Number(text);
      if (!Number.isFinite(number) || number < 0) {
        throw new Error("Некорректное значение «" + label + "»: " + String(value));
      }
      return text;
    }

    function gisCreateDate(value) {
      const text = String(value || "").trim();
      if (!text) return null;
      if (/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/.test(text)) {
        return text + ":00:000";
      }
      return text;
    }

    async function uploadAttachmentFile(photo) {
      const bytes = Uint8Array.from(
        atob(String(photo?.fileBase64 || "")),
        (char) => char.charCodeAt(0)
      );
      if (!bytes.length) {
        throw new Error("Передан пустой файл фотографии.");
      }
      const fileName = String(photo?.fileName || "photo.jpg");
      const description = String(photo?.description || fileName);
      const contentType = String(photo?.contentType || "image/jpeg");
      const blob = new Blob([bytes], { type: contentType });
      const size = blob.size;
      const identifier = size + "-" + fileName.replace(/[^\w-]/g, "");

      const form = new FormData();
      const values = {
        context: "homemanagement",
        description,
        fileName,
        chunkNumber: "1",
        totalChunks: "1",
        fileSize: String(size),
        flowChunkNumber: "1",
        flowChunkSize: "5242880",
        flowCurrentChunkSize: String(size),
        flowTotalSize: String(size),
        flowIdentifier: identifier,
        flowFilename: fileName,
        flowRelativePath: fileName,
        flowTotalChunks: "1"
      };
      for (const [key, value] of Object.entries(values)) form.append(key, value);
      form.append("file", blob, fileName);

      await assertOrganization();
      const response = await fetch(
        "/filestore/api/rest/services/filestore/uploadFile",
        { method: "POST", body: form, credentials: "include" }
      );
      if (!response.ok) {
        throw new Error("Не удалось загрузить фотографию в файловое хранилище ГИС ЖКХ.");
      }
      const uploaded = await response.json();
      if (!uploaded?.guid) {
        throw new Error("ГИС ЖКХ не вернул GUID загруженной фотографии.");
      }
      let details = uploaded;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        details = await request(
          "GET",
          "/filestore/api/rest/services/filestore/fileDetail?fileUid=" +
            encodeURIComponent(String(uploaded.guid)),
          undefined,
          STATE_WORKPLANNING
        );
        if (details?.infected) {
          throw new Error("ГИС ЖКХ сообщил, что загруженная фотография заражена.");
        }
        if (details?.virusChecked && details?.downloadAvailable) break;
        await sleep(500);
      }
      if (!details?.virusChecked || !details?.downloadAvailable) {
        throw new Error("ГИС ЖКХ слишком долго проверяет загруженную фотографию.");
      }

      return {
        attachment: {
          createDate: gisCreateDate(details.createDate),
          fileName: details.fileName || fileName,
          description: details.description || description,
          contentType: details.contentType || contentType,
          fileSize: Number(details.fileSize ?? size),
          contentGuid: uploaded.guid
        },
        workDate: { date: null }
      };
    }

    async function localPhotoForSave(photo) {
      if (photo?.remoteContentGuid) {
        const data = await request(
          "GET",
          "/filestore/api/rest/services/filestore/fileDetail?fileUid=" +
            encodeURIComponent(String(photo.remoteContentGuid)),
          undefined,
          STATE_WORKPLANNING
        );
        const result = {
          attachment: {
            createDate: gisCreateDate(data?.createDate),
            fileName: data?.fileName || photo.fileName || null,
            description:
              data?.description || photo.description || photo.fileName || null,
            contentType: data?.contentType || photo.contentType || null,
            fileSize: data?.fileSize ?? photo.fileSize ?? null,
            contentGuid: photo.remoteContentGuid
          },
          workDate: { date: null }
        };
        if (photo?.remotePhotoGuid) result.guid = photo.remotePhotoGuid;
        return result;
      }
      return uploadAttachmentFile(photo);
    }

    async function mergeManagedPhotos(localItem, remoteItem) {
      const managed = new Set(
        Array.isArray(localItem?.managedPhotoContentGuids)
          ? localItem.managedPhotoContentGuids.map(String)
          : []
      );
      const remote = Array.isArray(remoteItem?.photos) ? remoteItem.photos : [];
      const preserved = remote.filter((photo) => {
        const guid = photo?.attachment?.contentGuid;
        return !guid || !managed.has(String(guid));
      });
      const localPhotos = [];
      const mappings = [];
      for (const photo of Array.isArray(localItem?.photos) ? localItem.photos : []) {
        const existingRemote = photo?.remoteContentGuid
          ? remote.find(
              (remotePhoto) =>
                String(remotePhoto?.attachment?.contentGuid || "") ===
                String(photo.remoteContentGuid)
            )
          : null;
        const prepared = existingRemote || (await localPhotoForSave(photo));
        localPhotos.push(prepared);
        mappings.push({
          localPhotoId: photo?.localPhotoId || null,
          contentGuid:
            prepared?.attachment?.contentGuid ||
            photo?.remoteContentGuid ||
            null
        });
      }
      if (preserved.length + localPhotos.length > 1000) {
        throw new Error(
          "К одной работе нельзя прикрепить более 1000 фотографий."
        );
      }
      return { photos: preserved.concat(localPhotos), mappings };
    }

    async function getOrganizationWork(workCode, workGuid) {
      const url =
        "/orgnsi/api/rest/services/organizations/works?&code=" +
        encodeURIComponent(String(workCode || "")) +
        "&guid=" +
        encodeURIComponent(String(workGuid || ""));
      const data = await request("GET", url, undefined, STATE_WORKPLANNING);
      const item = Array.isArray(data) ? data[0] : data;
      if (!item?.guid) {
        throw new Error("Выбранная работа больше не найдена в справочнике организации.");
      }
      return item;
    }

    async function resolvePlannedWorkUnits(payload) {
      const rows = [];
      for (const localItem of Array.isArray(payload?.items) ? payload.items : []) {
        try {
          const work = await getOrganizationWork(
            localItem?.workCode,
            localItem?.workGuid
          );
          rows.push({
            localItemId: localItem?.localItemId || null,
            workGuid: work?.guid || localItem?.workGuid || null,
            workCode: String(work?.code || localItem?.workCode || ""),
            unitName:
              work?.dimensionsUnit?.dimensionsUnitOkei?.nationalSymbolName ||
              work?.dimensionsUnit?.dimensionsUnitString ||
              null
          });
        } catch (_) {
          rows.push({
            localItemId: localItem?.localItemId || null,
            workGuid: localItem?.workGuid || null,
            workCode: String(localItem?.workCode || ""),
            unitName: null
          });
        }
      }
      return { items: rows };
    }

    async function listOrganizationWorkOptions() {
      const roots = await request(
        "POST",
        "/orgnsi/api/rest/services/organizations/hierarchical/dictionaries/nodes/roots",
        {
          nsiObjectName: "OrganizationWork",
          actual: "ACTIVE",
          excludedCodes: ["0"]
        },
        STATE_WORKPLANNING
      );
      return (Array.isArray(roots) ? roots : [])
        .map((item) => ({
          guid: item?.guid || null,
          code: String(item?.code || item?.fieldValue1 || ""),
          name: item?.fieldValue2 || "",
          workTypeName: item?.fieldValue3 || "",
          unitName: item?.fieldValue4 || ""
        }))
        .filter((item) => item.guid && item.code && item.name);
    }

    async function listUnplannedWorkOptions(payload) {
      const options = await listOrganizationWorkOptions();
      const plannedGuids = new Set(
        (payload?.plannedWorkGuids || []).filter(Boolean).map(String)
      );
      const plannedCodes = new Set(
        (payload?.plannedWorkCodes || []).filter(Boolean).map(String)
      );
      return options.filter(
        (item) =>
          !plannedGuids.has(String(item?.guid || "")) &&
          !plannedCodes.has(String(item?.code || ""))
      );
    }

    async function buildFixedWorksSavePayload(localPayload, reportingPeriod, remoteData) {
      const remoteItems = Array.isArray(remoteData?.plannedWorkList)
        ? remoteData.plannedWorkList
        : [];
      const localItems = Array.isArray(localPayload?.items)
        ? localPayload.items
        : [];

      if (!remoteItems.length) {
        throw new Error("В выбранном периоде ГИС ЖКХ нет плановых работ.");
      }
      if (remoteItems.length !== localItems.length) {
        throw new Error(
          "Состав локальной фиксации отличается от состава периода в ГИС ЖКХ."
        );
      }

      const usedIndexes = new Set();
      const plannedMappings = [];
      const photoMappings = [];
      const plannedWorkList = [];
      for (const localItem of localItems) {
        const matched = fixationLocalItemMatch(localItem, remoteItems, usedIndexes);
        const remote = matched.item;
        const factCount =
          localItem?.factCount === null || localItem?.factCount === undefined
            ? null
            : Number(localItem.factCount);
        if (
          factCount !== null &&
          (!Number.isInteger(factCount) || factCount < 0)
        ) {
          throw new Error("Некорректное фактическое количество.");
        }

        const monthlyFixedWork =
          factCount === null
            ? null
            : {
                count: factCount,
                monthYear: remote?.monthlyFixedWork?.monthYear ?? null,
                dates: Array.isArray(remote?.monthlyFixedWork?.dates)
                  ? remote.monthlyFixedWork.dates
                  : []
              };

        const mergedPhotos = await mergeManagedPhotos(localItem, remote);
        photoMappings.push(...mergedPhotos.mappings);

        plannedWorkList.push({
          guid: remote?.guid || null,
          work: compactReference(remote?.work),
          workType: compactReference(remote?.workType),
          monthlyFixedWork,
          workPlanItemGuid: remote?.workPlanItemGuid || null,
          factPrice: fixationNumberForGis(localItem?.factPrice, "Цена факт"),
          factAmount: fixationNumberForGis(localItem?.factAmount, "Объем факт"),
          factTotalCost: fixationNumberForGis(
            localItem?.factTotalCost,
            "Стоимость факт"
          ),
          photos: mergedPhotos.photos
        });
        plannedMappings.push({
          localItemId: localItem?.localItemId || null,
          remoteIndex: matched.index
        });
      }

      const remoteUnplanned = Array.isArray(remoteData?.unplannedWorkList)
        ? remoteData.unplannedWorkList
        : [];
      const managedUnplanned = new Set(
        Array.isArray(localPayload?.managedUnplannedGuids)
          ? localPayload.managedUnplannedGuids.map(String)
          : []
      );
      const preservedUnplanned = remoteUnplanned.filter(
        (item) => !item?.guid || !managedUnplanned.has(String(item.guid))
      );

      const plannedGuids = new Set(
        remoteItems.map((item) => String(item?.work?.guid || "")).filter(Boolean)
      );
      const plannedCodes = new Set(
        remoteItems.map((item) => String(item?.work?.code || "")).filter(Boolean)
      );

      const newUnplanned = [];
      const unplannedMappings = [];
      for (const localItem of Array.isArray(localPayload?.unplannedItems)
        ? localPayload.unplannedItems
        : []) {
        if (
          plannedGuids.has(String(localItem?.workGuid || "")) ||
          plannedCodes.has(String(localItem?.workCode || ""))
        ) {
          throw new Error(
            "Выбранная работа, услуга является плановой. Укажите другую работу, услугу."
          );
        }

        const existing =
          remoteUnplanned.find(
            (item) =>
              localItem?.gisGuid &&
              String(item?.guid || "") === String(localItem.gisGuid)
          ) || {};
        const work = await getOrganizationWork(
          localItem?.workCode,
          localItem?.workGuid
        );
        const factCount =
          localItem?.factCount === null || localItem?.factCount === undefined
            ? null
            : Number(localItem.factCount);
        if (
          factCount !== null &&
          (!Number.isInteger(factCount) || factCount < 0)
        ) {
          throw new Error("Некорректное количество внеплановой работы.");
        }

        const mergedPhotos = await mergeManagedPhotos(localItem, existing);
        photoMappings.push(...mergedPhotos.mappings);
        const workType = {
          code: work?.serviceType?.code || localItem?.workTypeCode || null
        };

        const updatedMonthly =
          factCount === null
            ? null
            : {
                count: String(factCount),
                monthYear: {
                  month: Number(localPayload.month),
                  year: Number(localPayload.year)
                },
                dates: Array.isArray(existing?.monthlyFixedWork?.dates)
                  ? existing.monthlyFixedWork.dates
                  : []
              };

        function compactGuidOnly(value) {
          return value?.guid ? { guid: value.guid } : {};
        }

        // search/fixedworks expands work/workType into rich DTOs containing fields
        // such as rootEntityGuid. save/fixedworks accepts compact references only.
        // Never echo the expanded remote objects back on update.
        const unplannedRow = {
          work: { code: work.code, guid: work.guid },
          workType,
          monthlyFixedWork: updatedMonthly,
          accidentObject: compactGuidOnly(existing?.accidentObject),
          municipalService: compactGuidOnly(existing?.municipalService),
          municipalResource: compactGuidOnly(existing?.municipalResource),
          factPrice: fixationNumberForGis(localItem?.factPrice, "Цена факт"),
          factAmount: fixationNumberForGis(localItem?.factAmount, "Объем факт"),
          factTotalCost: fixationNumberForGis(
            localItem?.factTotalCost,
            "Стоимость факт"
          ),
          photos: mergedPhotos.photos,
          comment: localItem?.comment || null
        };
        if (existing?.guid) unplannedRow.guid = existing.guid;
        if (existing?.municipalResourceSupplierGuid) {
          unplannedRow.municipalResourceSupplierGuid =
            existing.municipalResourceSupplierGuid;
        }

        newUnplanned.push(unplannedRow);
        unplannedMappings.push({
          localItemId: localItem?.localItemId || null,
          workGuid: work.guid,
          existingGuid: existing?.guid || null,
          workTypeCode: work?.serviceType?.code || null,
          workTypeGuid: work?.serviceType?.guid || null,
          workTypeName: work?.serviceType?.serviceName || null,
          unitName:
            work?.dimensionsUnit?.dimensionsUnitOkei?.nationalSymbolName ||
            work?.dimensionsUnit?.dimensionsUnitString ||
            null
        });
      }

      return {
        body: {
          reportingPeriodGuid: reportingPeriod.guid,
          plannedWorkList,
          unplannedWorkList: preservedUnplanned.concat(newUnplanned),
          acts: Array.isArray(remoteData?.acts) ? remoteData.acts : []
        },
        plannedMappings,
        unplannedMappings,
        photoMappings
      };
    }

    const payload = command?.payload || {};

    if (command.type === "keep_alive") {
      // The GIS timeout is partly driven by client-side inactivity, so a server
      // echo alone is not sufficient. Dispatch the same harmless activity
      // signals the page's idle watcher listens for, then perform an
      // authenticated request as an additional server-side keepalive.
      const activityTarget = document.body || document.documentElement || document;
      for (const eventName of ["mousemove", "scroll"]) {
        activityTarget.dispatchEvent(
          new Event(eventName, { bubbles: true, cancelable: false })
        );
        document.dispatchEvent(
          new Event(eventName, { bubbles: true, cancelable: false })
        );
      }

      // Recover the page's native timeout warning before probing the session:
      // the echo itself may fail until that continuation action has run.
      const continueButton = Array.from(document.querySelectorAll("button")).find(
        (button) => (button.textContent || "").trim() === "Продолжить работу" &&
          !button.disabled && button.getClientRects().length > 0
      );
      if (continueButton) {
        continueButton.click();
      }

      const echo = (await $http({
        method: "GET",
        url: "/auth/api/rest/services/auth/echo/",
        headers: { "State-GUID": STATE_WORKPLANNING },
        cache: false,
        timeout: 15000
      })).data;
      // A redirected login/maintenance document can arrive with HTTP 200.
      // Do not require any undocumented positive field in ordinary echo data.
      if (echo === false ||
          (typeof echo === "string" && /<\s*(?:!doctype\s+html|html|head|body|form)\b/i.test(echo)) ||
          echo?.ok === false || echo?.success === false || echo?.authenticated === false) {
        throw new Error(
          "ГИС ЖКХ не подтвердила сессию. Проверьте личный кабинет в браузере."
        );
      }

      return {
        ok: true,
        result: {
          keptAliveAt: new Date().toISOString(),
          timeoutDialogWasVisible: Boolean(continueButton)
        }
      };
    }

    if (command.type === "ping") {
      return {
        ok: true,
        result: {
          pageUrl: location.href,
          pageTitle: document.title
        }
      };
    }

    if (command.type === "get_current_organization") {
      return { ok: true, result: await getCurrentOrganization() };
    }

    // The agreements workflow uses distinct agreement/version/object/protocol IDs.
    // Keep it separate from workplanning and from local calculation tariffs.
    const feeBase = "/agreements/api/rest/services/agreements/management/payment";
    function feeDate(value) {
      const text = String(value || "").trim();
      let match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
      if (!match) {
        const local = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
        if (local) match = [text, local[3], local[2], local[1]];
      }
      if (!match) throw new Error("В ответе ГИС отсутствует корректная дата периода платы.");
      const iso = `${match[1]}-${match[2]}-${match[3]}`;
      const parsed = new Date(iso + "T00:00:00Z");
      if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== iso) {
        throw new Error("В периоде платы указана недопустимая дата.");
      }
      return iso;
    }
    function feeLocalDate(value) {
      const [year, month, day] = feeDate(value).split("-");
      return `${day}.${month}.${year}`;
    }
    function feeAmount(value) {
      const text = String(value ?? "").trim().replace(/,/g, ".");
      if (!/^\d{1,10}(\.\d{1,2})?$/.test(text) || Number(text) <= 0) {
        throw new Error("Укажите положительный размер платы с точностью до копеек.");
      }
      return Number(text).toFixed(2);
    }
    function feeState(contract, mode = "view", guid = "") {
      const root = `/agreements/contract/MANAGEMENT/view/${encodeURIComponent(contract.versionGuid)}`;
      return guid ? `${root}/kirp/payment/${mode}/${encodeURIComponent(guid)}` : `${root}/BY_PROTOCOL/kirp/payment/add`;
    }
    function feeObjectMatches(object, fias) {
      return object?.fiasHouseAddress?.houseRef?.code === fias;
    }
    function feeOrganizationMatches(contract, organization) {
      return Boolean((contract.managementOrgOgrn || contract.managementOrg?.ogrn) === organization.ogrn &&
        (!contract.managementOrgRootGuid || !organization.organizationRootGuid ||
         contract.managementOrgRootGuid === organization.organizationRootGuid));
    }
    async function feeContract(payload) {
      const selected = payload.contract;
      if (!selected?.guid || !selected?.versionGuid || !selected?.objectRootGuid ||
          !payload.fiasHouseGuid || selected.fiasHouseGuid !== payload.fiasHouseGuid) {
        throw new Error("Загрузите договор управления выбранного дома.");
      }
      const organization = (await getCurrentOrganization()).organization;
      const dto = await request("GET", "/agreements/api/rest/services/agreements/managements/versions/" +
        encodeURIComponent(selected.versionGuid), undefined,
        `/agreements/contract/view/${encodeURIComponent(selected.versionGuid)}/info`);
      const objects = (dto.managedObjectList || []).filter(item =>
        item.rootEntityGuid === selected.objectRootGuid && feeObjectMatches(item, payload.fiasHouseGuid));
      if (dto.guid !== selected.guid || dto.versionInfoDTO?.guid !== selected.versionGuid ||
          dto.agreementType !== "MANAGEMENT" || !dto.published ||
          !feeOrganizationMatches(dto, organization) || objects.length !== 1) {
        throw new Error("Договор или объект управления не соответствует выбранному дому и УО. Обновите данные из ГИС.");
      }
      const object = objects[0];
      const starts = [dto.effectiveDate, object.managedDateStart].filter(Boolean).map(feeDate).sort();
      const ends = [dto.endDate, object.managedDateEnd].filter(Boolean).map(feeDate).sort();
      return {guid: dto.guid, versionGuid: dto.versionInfoDTO.guid,
        objectRootGuid: object.rootEntityGuid, fiasHouseGuid: payload.fiasHouseGuid,
        number: dto.number || "Без номера", date: dto.signDate ? feeDate(dto.signDate) : "",
        from: starts.at(-1) || "", to: ends[0] || "", status: dto.agreementStatus || dto.dbStatus};
    }
    async function feeContracts(payload) {
      if (!payload.fiasHouseGuid) throw new Error("У дома нет кода ФИАС. Загрузите дом из ГИС.");
      const organization = (await getCurrentOrganization()).organization;
      const result = [];
      for (let page = 1; page <= 1000; page++) {
        const rows = await request("POST", "/agreements/api/rest/services/agreements/documents/search",
          {addressSearchCriteria: {houseRef: {guid: payload.fiasHouseGuid, code: payload.fiasHouseGuid}},
           agreementStatusList: [], autoprolongation: null, pageIndex: page, elementsPerPage: 100},
          "/agreements");
        if (!Array.isArray(rows)) throw new Error("ГИС вернула неизвестный формат реестра договоров.");
        for (const dto of rows) {
          if (dto.agreementType !== "MANAGEMENT" || !dto.published || !feeOrganizationMatches(dto, organization)) continue;
          for (const object of dto.managedObjectList || []) {
            if (!feeObjectMatches(object, payload.fiasHouseGuid) || !object.rootEntityGuid) continue;
            const contract = await feeContract({...payload, contract: {guid: dto.guid,
              versionGuid: dto.versionInfoDTO?.guid, objectRootGuid: object.rootEntityGuid,
              fiasHouseGuid: payload.fiasHouseGuid}});
            if (!result.some(item => item.versionGuid === contract.versionGuid &&
                item.objectRootGuid === contract.objectRootGuid)) result.push(contract);
          }
        }
        const total = Number(rows[0]?.totalCount);
        if (rows.length < 100 || (Number.isFinite(total) && page * 100 >= total)) return result;
      }
      throw new Error("Слишком много договоров. Загрузка остановлена без изменения локальных данных.");
    }
    function feePage(response, key, message) {
      const raw = response?.[key];
      const total = typeof raw === "number" || (typeof raw === "string" && /^\d+$/.test(raw)) ? Number(raw) : NaN;
      const items = response?.items ?? (total === 0 ? [] : null);
      if (!Array.isArray(items) || !Number.isSafeInteger(total) || total < 0 ||
          (total === 0 && items.length) || (total > 0 && !items.length)) throw new Error(message);
      return {items, total};
    }
    async function feeProtocols(payload, contract) {
      const result = [];
      for (let page = 1; page <= 1000; page++) {
        const response = await request("POST", `/voting/api/rest/services/protocol/search-lite;page=${page};itemsPerPage=10`,
          {fiasHouseCodes: [payload.fiasHouseGuid], eligible: true, meetingTypes: ["OWNERS"],
           questionTypeCodes: ["12.1", "15.1"], decisionIsMade: true}, feeState(contract));
        const {items, total} = feePage(response, "total", "Не удалось прочитать список протоколов ГИС.");
        for (const item of items) {
          if (!item.cancelled && item.eligible && item.meetingType === "OWNERS" &&
              item.fiasHouseCodes?.includes(payload.fiasHouseGuid) && item.votingGuid && item.votingVersionGuid) {
            result.push({votingGuid: item.votingGuid, votingVersionGuid: item.votingVersionGuid,
              protocolCode: item.protocolCode || "Без номера", protocolDate: feeDate(item.protocolDate),
              fiasHouseCodes: item.fiasHouseCodes, eligible: true, cancelled: false});
          }
        }
        if (page * 10 >= total) return result;
      }
      throw new Error("Слишком много протоколов. Уточните выбор в ГИС.");
    }
    function normalizeFee(dto) {
      if (!dto?.guid || !dto.lastUpdate || !["DRAFT", "APPROVED", "TERMINATED"].includes(dto.status)) {
        throw new Error("ГИС не подтвердила состояние сведений о плате.");
      }
      return {guid: dto.guid, lastUpdate: dto.lastUpdate, agreementRootGuid: dto.agreementRootGuid,
        managedObjectRootGuid: dto.managedObjectRootGuid, status: dto.status, type: dto.type,
        protocolFromVoting: dto.protocolFromVoting, managementPayment: dto.managementPayment,
        hasOtherDetails: Boolean(dto.fromExternalSystem || dto.paymentInfoOfSelfOwners ||
          [dto.managementWorkPaymentInfo, dto.ownersAssociationMembersProtocols,
           dto.selfOwnersProtocols].some(items => items?.length)),
        hasScanBasis: Boolean(dto.managementProtocols?.length),
        versionInfo: dto.versionInfo, ownerVotingGuidList: dto.ownerVotingGuidList || [],
        period: {from: feeDate(dto.period?.from), to: feeDate(dto.period?.to)},
        ownerVotingList: (dto.ownerVotingList || []).map(item => ({votingGuid: item.votingGuid,
          votingVersionGuid: item.votingVersionGuid, number: item.number, date: feeDate(item.date)})),
        cancellationReasonComment: dto.cancellationReasonComment || ""};
    }
    function assertFeeScope(dto, contract, guid = null) {
      if ((guid && dto?.guid !== guid) || dto?.agreementRootGuid !== contract.guid ||
          dto?.managedObjectRootGuid !== contract.objectRootGuid) {
        throw new Error("Сведения о плате относятся к другому договору или дому.");
      }
    }
    async function feeDetails(guid, contract) {
      const dto = await request("GET", feeBase + "/" + encodeURIComponent(guid), undefined,
        feeState(contract, "view", guid));
      assertFeeScope(dto, contract, guid);
      return dto;
    }
    async function feeRecords(contract) {
      const result = [];
      for (let page = 1; page <= 1000; page++) {
        const response = await request("POST", `${feeBase}/search;page=${page};itemsPerPage=100`,
          {agreementRootGuid: contract.guid, agreementVersionGuid: contract.versionGuid,
           statuses: [], pageIndex: page, elementsPerPage: 100}, feeState(contract));
        // A terminated agreement with no fees may return items: null (or omit it).
        // Accept that shape only with an explicit numeric zero, never an absent count.
        const {items, total} = feePage(response, "totalCount", "Не удалось прочитать список сведений о плате.");
        for (const item of items) {
          // A contract can contain other houses. Never import them into the selected house.
          if (item.managedObjectRootGuid !== contract.objectRootGuid) continue;
          const dto = await feeDetails(item.guid, contract);
          result.push(normalizeFee(dto));
        }
        if (page * 100 >= total) return result;
      }
      throw new Error("Слишком много сведений о плате.");
    }
    function feeMatches(dto, desired) {
      const basis = dto.ownerVotingList || [];
      return dto.type === "BY_PROTOCOL" && dto.protocolFromVoting === true &&
        feeDate(dto.period?.from) === desired.start && feeDate(dto.period?.to) === desired.end &&
        feeAmount(dto.managementPayment) === desired.amount &&
        dto.ownerVotingGuidList?.length === 1 && dto.ownerVotingGuidList[0] === desired.protocol.votingGuid &&
        basis.length === 1 && basis[0].votingGuid === desired.protocol.votingGuid &&
        basis[0].votingVersionGuid === desired.protocol.votingVersionGuid;
    }
    async function feeCommand(payload, type) {
      if (!payload.organizationContext?.ogrn) throw new Error("Выберите УО перед обменом сведениями о плате.");
      if (type === "save_and_approve_management_fee") {
        // The GIS UI's direct-placement action is also save -> read -> approval.
        // These are two server operations, not one atomic mutation.
        const saved = await feeCommand(payload, "save_management_fee_project");
        if (saved.record.status === "APPROVED") return saved;
        try {
          return await feeCommand({...payload, contract: saved.contract,
            remoteGuid: saved.record.guid, lastUpdate: saved.record.lastUpdate}, "approve_management_fee");
        } catch (error) {
          throw new Error("Проект сохранён в ГИС. Размещение не подтверждено. " +
            (error?.message || error?.data?.message || "Загрузите сведения и проверьте состояние проекта."));
        }
      }
      if (type === "load_management_fees") {
        const contracts = await feeContracts(payload), records = [], contractErrors = [];
        for (const contract of contracts) {
          try {
            for (const record of await feeRecords(contract)) records.push({contractVersionGuid: contract.versionGuid, record});
          } catch (error) {
            contractErrors.push({contractVersionGuid: contract.versionGuid, objectRootGuid: contract.objectRootGuid,
              number: contract.number, message: String(error?.message || "Не удалось прочитать сведения договора.").slice(0, 500)});
          }
        }
        if (contracts.length && contractErrors.length === contracts.length) {
          throw new Error(contractErrors.map(item => `Договор № ${item.number}: ${item.message}`).join("; "));
        }
        const protocolContract = contracts.find(item => item.status !== "TERMINATED") || contracts[0];
        const protocols = protocolContract ? await feeProtocols(payload, protocolContract) : [];
        return {contracts, protocols, records, contractErrors};
      }
      const contract = await feeContract(payload);
      if (type === "load_fee_protocols") return {protocols: await feeProtocols(payload, contract)};
      const desired = payload.document;
      if (type === "save_management_fee_project") {
        desired.amount = feeAmount(desired.amount);
        desired.start = feeDate(desired.start); desired.end = feeDate(desired.end);
        if (desired.start > desired.end) throw new Error("Окончание периода платы не может быть раньше начала.");
        if ((contract.from && desired.start < contract.from) || (contract.to && desired.end > contract.to)) {
          throw new Error(`Период платы ${feeLocalDate(desired.start)} — ${feeLocalDate(desired.end)} ` +
            `выходит за срок управления домом по договору: ${contract.from ? feeLocalDate(contract.from) : "не указано"} — ` +
            `${contract.to ? feeLocalDate(contract.to) : "не указано"}. Обновите сведения о договоре и проверьте даты.`);
        }
        const protocols = await feeProtocols(payload, contract);
        const protocol = protocols.find(item => item.votingGuid === desired.protocol?.votingGuid &&
          item.votingVersionGuid === desired.protocol?.votingVersionGuid &&
          item.protocolCode === desired.protocol?.protocolCode && item.protocolDate === desired.protocol?.protocolDate);
        if (!protocol) throw new Error("Выбранный протокол больше недоступен. Обновите список оснований.");
        let current = null;
        if (payload.remoteGuid) {
          current = await feeDetails(payload.remoteGuid, contract);
          if (feeMatches(current, desired) && ["DRAFT", "APPROVED"].includes(current.status)) {
            return {record: normalizeFee(current), contract};
          }
          if (!["DRAFT", "APPROVED"].includes(current.status) || !current.lastUpdate || current.lastUpdate !== payload.lastUpdate ||
              current.type !== "BY_PROTOCOL" || normalizeFee(current).hasOtherDetails) {
            throw new Error("Проект изменён в ГИС или использует другой способ выбора основания. Загрузите актуальные сведения.");
          }
        } else {
          const existing = (await feeRecords(contract)).filter(item => item.status !== "TERMINATED" &&
            item.period.from === desired.start && item.period.to === desired.end);
          if (existing.length === 1 && feeMatches(existing[0], desired)) return {record: existing[0], contract};
          if (existing.length) throw new Error("За этот период уже есть сведения в ГИС. Загрузите их перед повторным сохранением.");
        }
        const body = {...(current || {}), agreementRootGuid: contract.guid,
          // Recorded PUT keeps APPROVED in the request. GIS creates DRAFT v+1.
          agreementType: "MANAGEMENT", type: "BY_PROTOCOL", status: current?.status || "DRAFT",
          versionInfo: current?.versionInfo || {versionNumber: "1", versionDateTime:
            `${String(new Date().getDate()).padStart(2, "0")}.${String(new Date().getMonth() + 1).padStart(2, "0")}.${new Date().getFullYear()}`},
          period: {from: feeLocalDate(desired.start), to: feeLocalDate(desired.end)},
          managementPayment: desired.amount, managementWorkPaymentInfo: [], managementProtocols: [],
          ownersAssociationMembersProtocols: [], selfOwnersProtocols: [],
          paymentInfoOfOwnersAssociationMembers: true, managedObjectRootGuid: contract.objectRootGuid,
          protocolFromVoting: true, ownerVotingGuidList: [protocol.votingGuid]};
        for (const key of ["ownerVotingList", "organization", "address", "paymentForAllHouses"]) delete body[key];
        const response = await request(current ? "PUT" : "POST", feeBase + "/", body,
          feeState(contract, current ? "edit" : "view", current?.guid || ""), true);
        const guid = current?.guid || (typeof response === "string" ? response.replace(/^"|"$/g, "").trim() : response?.guid);
        if (!guid) throw new Error("Сохранение отправлено, но ГИС не вернула номер проекта. Загрузите сведения перед повторной отправкой.");
        const saved = await feeDetails(guid, contract);
        if (saved.status !== "DRAFT" || !feeMatches(saved, desired)) {
          throw new Error("ГИС не подтвердила сохранение проекта с указанными данными. Загрузите сведения.");
        }
        return {record: normalizeFee(saved), contract};
      }
      const current = await feeDetails(payload.remoteGuid, contract);
      if (type === "approve_management_fee") {
        if (current.status === "APPROVED") {
          if (!feeMatches(current, {...desired, amount: feeAmount(desired.amount),
              start: feeDate(desired.start), end: feeDate(desired.end)})) {
            throw new Error("Размещённые сведения не совпадают с изменениями. Сначала сохраните проект новой версии.");
          }
          return {record: normalizeFee(current), contract};
        }
        if (current.status !== "DRAFT" || !current.lastUpdate || current.lastUpdate !== payload.lastUpdate) {
          throw new Error("Состояние проекта изменилось в ГИС. Загрузите сведения перед размещением.");
        }
        if (current.type !== "BY_PROTOCOL" || !current.protocolFromVoting || current.ownerVotingGuidList?.length !== 1 || normalizeFee(current).hasOtherDetails) {
          throw new Error("Этот проект использует другой способ выбора основания. Разместите его в ГИС.");
        }
        const protocols = await feeProtocols(payload, contract);
        if (!feeMatches(current, {...desired, amount: feeAmount(desired.amount),
             start: feeDate(desired.start), end: feeDate(desired.end)}) ||
            !protocols.some(item => item.votingGuid === desired.protocol?.votingGuid &&
              item.votingVersionGuid === desired.protocol?.votingVersionGuid)) {
          throw new Error("Основание проекта недоступно. Обновите протокол перед размещением.");
        }
        await request("PUT", feeBase + "/approval", {entityGuid: current.guid, lastUpdate: current.lastUpdate},
          feeState(contract, "edit", current.guid));
        const saved = await feeDetails(current.guid, contract);
        if (saved.status !== "APPROVED") throw new Error("ГИС не подтвердила размещение. Загрузите сведения перед повторной отправкой.");
        return {record: normalizeFee(saved), contract};
      }
      if (type === "annul_management_fee") {
        if (current.status === "TERMINATED") return {record: normalizeFee(current), contract};
        if (current.status !== "APPROVED" || !current.lastUpdate || current.lastUpdate !== payload.lastUpdate || !String(payload.reason || "").trim()) {
          throw new Error("Укажите причину и загрузите актуальные сведения о плате перед аннулированием.");
        }
        await request("PUT", feeBase + "/termination", {rootGuid: current.guid,
          reason: String(payload.reason).trim(), lastUpdate: current.lastUpdate}, feeState(contract, "view", current.guid));
        const saved = await feeDetails(current.guid, contract);
        if (saved.status !== "TERMINATED") throw new Error("ГИС не подтвердила аннулирование. Загрузите сведения.");
        return {record: normalizeFee(saved), contract};
      }
      throw new Error("Неизвестная операция со сведениями о плате.");
    }

    // Recheck inside the selected page: tab navigation or organization switching
    // may have happened after discovery. Writes are also checked by request().
    await assertOrganization();

    if (["load_management_fees", "load_fee_protocols", "save_management_fee_project", "save_and_approve_management_fee",
         "approve_management_fee", "annul_management_fee"].includes(command.type)) {
      return {ok: true, result: await feeCommand(command.payload || {}, command.type)};
    }

    if (command.type === "get_organization_act_signatory") {
      const details = await request("GET", "/ppa/api/rest/services/ppa/organizations/details",
        undefined, "/organization/information");
      const fullName = [details?.chiefLastName, details?.chiefFirstName, details?.chiefMiddleName]
        .filter(value => typeof value === "string" && value.trim()).map(value => value.trim()).join(" ");
      if (!fullName || !details?.ogrn || !details?.guid) {
        throw new Error("В ГИС ЖКХ не заполнены сведения о руководителе организации.");
      }
      const additional = await request("POST", "/ppa/api/rest/services/ppa/organizations/additionalinfo",
        {organizationGuids: [details.guid]}, "/organization/information");
      const rows = additional?.additionalInfos || [];
      const chief = rows.length === 1 ? rows[0]?.chiefInfo : null;
      const normalizeName = value => String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
      const position = normalizeName(chief?.fio) === normalizeName(fullName) ? chief?.position : null;
      const candidates = [{fullName, position: typeof position === "string" ? position.trim() : "",
        source: "Реквизиты УО"}];
      if (typeof chief?.fio === "string" && chief.fio.trim() && normalizeName(chief.fio) !== normalizeName(fullName)) {
        candidates.push({fullName: chief.fio.trim(), position: String(chief.position || "").trim(),
          source: "Дополнительные сведения УО"});
      }
      await assertOrganization();
      return {ok: true, result: {ogrn: String(details.ogrn), candidates}};
    }

    if (command.type === "get_house_management_info") {
      return {
        ok: true,
        result: await getPublicManagementInfo(payload)
      };
    }

    if (command.type === "list_house_management_info") {
      return {
        ok: true,
        result: await listPublicManagementInfo(payload)
      };
    }

    if (command.type === "list_managed_houses") {
      return {
        ok: true,
        result: await listManagedHouses(payload)
      };
    }

    if (command.type === "list_work_lists") {
      return {
        ok: true,
        result: await listOrganizationWorkLists(payload)
      };
    }

    if (command.type === "download_work_list_export_template") {
      return {
        ok: true,
        result: await downloadWorkListExportTemplate(payload)
      };
    }

    if (command.type === "sync_work_list") {
      if (payload?.workListGuid) {
        try {
          const item = await getWorkListDetailAny(payload.workListGuid);
          return { ok: true, result: statusResult(item) };
        } catch (_) {
          // Fall back to period search for legacy local rows without a valid GUID.
        }
      }
      const item = await searchExactWorkList(payload, null);
      return { ok: true, result: statusResult(item) };
    }

    if (command.type === "cancel_work_list") {
      return {
        ok: true,
        result: await cancelWorkList(payload)
      };
    }

    if (command.type === "save_editable_work_list") {
      return {
        ok: true,
        result: await saveEditableWorkList(payload)
      };
    }

    if (command.type === "upload_import_work_list") {
      const fileGuid = await uploadFile(payload);
      await waitFileReady(fileGuid);
      const importJobGuid = await importFile(fileGuid);

      let draft = null;
      for (let attempt = 0; attempt < 60; attempt += 1) {
        draft = await searchExactWorkList(payload, ["DRAFT"]);
        if (draft) break;
        await sleep(1500);
      }

      if (!draft) {
        return {
          ok: true,
          result: {
            found: false,
            gisStatus: "IMPORT_SENT",
            workListGuid: null,
            fileGuid,
            importJobGuid
          }
        };
      }

      return {
        ok: true,
        result: statusResult(draft, { fileGuid, importJobGuid })
      };
    }


    async function searchQualityAssessment(reportingPeriodGuid, readOnly = false) {
      return request(
        "POST",
        "/workplanning/api/rest/services/search/estimations",
        {
          workReportingPeriodGuid: reportingPeriodGuid,
          readOnly: Boolean(readOnly),
          notInStatuses: ["CANCELLED"]
        },
        STATE_WORKPLANNING
      );
    }

    async function searchQualityActs(estimationItemGuid) {
      const data = await request(
        "POST",
        "/workplanning/api/rest/services/search/quality-acts",
        { estimationItemGuid: String(estimationItemGuid || "") },
        STATE_WORKPLANNING
      );
      return Array.isArray(data?.actList) ? data.actList : [];
    }

    function qualityDateForImport(value) {
      const text = String(value || "").trim();
      if (!text) return "";
      const match = /^(\d{2}\.\d{2}\.\d{4})/.exec(text);
      return match ? match[1] : text;
    }

    function normalizeQualityActForImport(act) {
      const attachment = act?.attachment || {};
      return {
        guid: act?.guid || null,
        actType: act?.actType || "ACCEPTANCE_ACT",
        number: String(act?.number || ""),
        regDate: qualityDateForImport(act?.regDate),
        periodFrom: qualityDateForImport(act?.period?.from),
        periodTo: qualityDateForImport(act?.period?.to),
        contentGuid: attachment?.contentGuid || null,
        fileName: attachment?.fileName || "Файл акта из ГИС ЖКХ",
        description: attachment?.description || null,
        contentType: attachment?.contentType || "application/octet-stream",
        fileSize: Number(attachment?.fileSize || 0)
      };
    }

    async function loadQualityAssessment(payload) {
      let reportingPeriod = null;
      try {
        reportingPeriod = await findReportingPeriod(payload);
      } catch (_) {
        return { found: false };
      }

      const remote = await searchQualityAssessment(reportingPeriod.guid, false);
      const estimation = remote?.estimation || null;
      if (!estimation?.guid) {
        return {
          found: false,
          reportingPeriodGuid: reportingPeriod.guid
        };
      }

      const rows = Array.isArray(remote?.estimationList)
        ? remote.estimationList
        : [];
      const items = [];
      for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index];
        let acts = [];
        if (row?.guid && row?.hasQualityActs) {
          acts = (await searchQualityActs(row.guid)).map(
            normalizeQualityActForImport
          );
        }
        items.push({
          guid: row?.guid || null,
          position: index + 1,
          workGuid: row?.work?.guid || row?.workGuid || null,
          workCode: row?.work?.code || null,
          workName: row?.workName || row?.work?.workName || "",
          cost: row?.cost ?? null,
          daysCount:
            row?.daysCount === null || row?.daysCount === undefined
              ? null
              : Number(row.daysCount),
          daysPerMonth: Number(row?.daysPerMonth || 0),
          costReduction: row?.costReduction ?? null,
          acts
        });
      }

      return {
        found: true,
        reportingPeriodGuid: reportingPeriod.guid,
        estimationGuid: estimation.guid,
        gisStatus: estimation.status || estimation.dbStatus || "DRAFT",
        items
      };
    }

    async function getQualityActByGuid(
      actGuid,
      orgRootGuid,
      houseGuid
    ) {
      return request(
        "POST",
        "/workplanning/api/rest/services/search/quality-act",
        {
          guid: String(actGuid || ""),
          orgRootGuid: String(orgRootGuid || ""),
          houseGuid: String(houseGuid || "")
        },
        STATE_WORKPLANNING
      );
    }

    function matchQualityItem(localItem, remoteItems) {
      const workGuid = String(localItem?.workGuid || "");
      const workCode = String(localItem?.workCode || "");
      return (
        remoteItems.find(
          (item) => workGuid && String(item?.work?.guid || "") === workGuid
        ) ||
        remoteItems.find(
          (item) => workCode && String(item?.work?.code || "") === workCode
        ) ||
        null
      );
    }

    function qualityDecimalText(value, label) {
      if (value === null || value === undefined || String(value).trim() === "") {
        return null;
      }
      const text = String(value)
        .replace(/\u00a0/g, "")
        .replace(/\s+/g, "")
        .replace(",", ".");
      const number = Number(text);
      if (!Number.isFinite(number) || number < 0) {
        throw new Error("Некорректное значение «" + label + "»: " + String(value));
      }
      return text;
    }

    function qualityActDateTime(value) {
      const text = String(value || "").trim();
      if (!text) return text;
      if (/^\d{2}\.\d{2}\.\d{4}$/.test(text)) {
        return text + " 00:00:00:000";
      }
      return text;
    }

    function normalizeRemoteQualityAct(act) {
      const result = Object.assign({}, act || {});
      result.regDate = qualityActDateTime(result.regDate);
      if (result.attachment) {
        result.attachment = Object.assign({}, result.attachment);
      }
      if (result.period) {
        result.period = Object.assign({}, result.period);
      }
      return result;
    }

    function remoteQualityActMatchesLocal(localAct, remoteAct) {
      const remoteGuid = String(remoteAct?.guid || "");
      const contentGuid = String(remoteAct?.attachment?.contentGuid || "");
      const localRemoteGuid = String(localAct?.remoteActGuid || "");
      const localContentGuid = String(localAct?.remoteContentGuid || "");
      // The act GUID identifies the document; a reused attachment must never
      // redirect a known act to another document.
      if (localRemoteGuid) return remoteGuid === localRemoteGuid;
      if (localContentGuid && contentGuid === localContentGuid) return true;
      return false;
    }

    function qualityActDateKey(value) {
      const text = String(value || "").trim();
      const russian = /^(\d{2})\.(\d{2})\.(\d{4})(?:$|[ T])/.exec(text);
      const iso = /^(\d{4})-(\d{2})-(\d{2})(?:$|[ T])/.exec(text);
      if (!russian && !iso) return null;
      const [year, month, day] = russian
        ? [Number(russian[3]), Number(russian[2]), Number(russian[1])]
        : [Number(iso[1]), Number(iso[2]), Number(iso[3])];
      const date = new Date(0);
      date.setUTCFullYear(year, month - 1, day);
      if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 ||
          date.getUTCDate() !== day) return null;
      // Compare the written calendar date, without timezone conversion.
      return [year, month, day].join("-");
    }

    function qualityActMetadataMatchesLocal(localAct, remoteAct) {
      const localDate = qualityActDateKey(localAct?.regDate);
      return Boolean(localDate) &&
        qualityActDateKey(remoteAct?.regDate) === localDate &&
        String(remoteAct?.number || "") === String(localAct?.number || "") &&
        String(remoteAct?.actType || "") === String(localAct?.actType || "") &&
        String(remoteAct?.period?.from || "") === String(localAct?.periodFrom || "") &&
        String(remoteAct?.period?.to || "") === String(localAct?.periodTo || "");
    }

    async function uploadQualityActFile(act) {
      const encoded = String(act?.fileBase64 || "");
      if (!encoded) {
        throw new Error("Не передан файл акта.");
      }
      const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
      if (!bytes.length) throw new Error("Файл акта пуст.");
      if (bytes.length > 50 * 1024 * 1024) {
        throw new Error("Размер файла акта превышает 50 МБ.");
      }

      const fileName = String(act?.fileName || "act.pdf");
      const description = String(act?.description || fileName);
      const contentType = String(act?.contentType || "application/octet-stream");
      const blob = new Blob([bytes], { type: contentType });
      const identifier = blob.size + "-" + fileName.replace(/[^\w-]/g, "");

      const form = new FormData();
      const values = {
        context: "homemanagement",
        description,
        fileName,
        chunkNumber: "1",
        totalChunks: "1",
        fileSize: String(blob.size),
        flowChunkNumber: "1",
        flowChunkSize: "5242880",
        flowCurrentChunkSize: String(blob.size),
        flowTotalSize: String(blob.size),
        flowIdentifier: identifier,
        flowFilename: fileName,
        flowRelativePath: fileName,
        flowTotalChunks: "1"
      };
      for (const [key, value] of Object.entries(values)) form.append(key, value);
      form.append("file", blob, fileName);

      await assertOrganization();
      const response = await fetch(
        "/filestore/api/rest/services/filestore/uploadFile",
        { method: "POST", body: form, credentials: "include" }
      );
      if (!response.ok) {
        throw new Error("Не удалось загрузить акт в файловое хранилище ГИС ЖКХ.");
      }
      const uploaded = await response.json();
      if (!uploaded?.guid) {
        throw new Error("ГИС ЖКХ не вернул GUID загруженного акта.");
      }

      let details = uploaded;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        details = await request(
          "GET",
          "/filestore/api/rest/services/filestore/fileDetail?fileUid=" +
            encodeURIComponent(String(uploaded.guid)),
          undefined,
          STATE_WORKPLANNING
        );
        if (details?.infected) {
          throw new Error("ГИС ЖКХ сообщил, что файл акта заражён.");
        }
        if (details?.virusChecked && details?.downloadAvailable) break;
        await sleep(500);
      }
      if (!details?.virusChecked || !details?.downloadAvailable) {
        throw new Error("ГИС ЖКХ слишком долго проверяет файл акта.");
      }

      return {
        contentGuid: uploaded.guid,
        attachment: {
          contentType: details?.contentType || contentType,
          contentGuid: uploaded.guid,
          fileSize: Number(details?.fileSize ?? blob.size),
          fileName: details?.fileName || fileName,
          description: details?.description || description
        }
      };
    }

    async function qualityActAttachment(localAct) {
      if (!localAct?.remoteContentGuid) {
        return uploadQualityActFile(localAct);
      }

      const contentGuid = String(localAct.remoteContentGuid);
      const details = await request(
        "GET",
        "/filestore/api/rest/services/filestore/fileDetail?fileUid=" +
          encodeURIComponent(contentGuid),
        undefined,
        STATE_WORKPLANNING
      );
      if (details?.infected) {
        throw new Error("ГИС ЖКХ сообщил, что файл акта заражён.");
      }
      return {
        contentGuid,
        attachment: {
          contentType: details?.contentType || localAct?.contentType || null,
          contentGuid,
          fileSize: Number(details?.fileSize ?? localAct?.fileSize ?? 0),
          fileName: details?.fileName || localAct?.fileName || null,
          description:
            details?.description ||
            localAct?.description ||
            localAct?.fileName ||
            null
        }
      };
    }

    async function createLocalQualityAct(localAct, orgRootGuid, house) {
      const actType = String(localAct?.actType || "");
      if (!["ACCEPTANCE_ACT", "QUALITY_VIOLATION_ACT"].includes(actType)) {
        throw new Error("Неизвестный тип акта оценки качества.");
      }

      await request(
        "POST",
        "/workplanning/api/rest/services/checkunique/quality-act",
        {
          regDate: qualityActDateTime(localAct?.regDate),
          number: String(localAct?.number || ""),
          actType,
          orgRootGuid: String(orgRootGuid || ""),
          houseGuid: house.guid
        },
        STATE_WORKPLANNING
      );

      const uploaded = await qualityActAttachment(localAct);
      return {
        localActId: localAct?.localActId || null,
        contentGuid: uploaded.contentGuid,
        act: {
          guid: null,
          regDate: qualityActDateTime(localAct?.regDate),
          number: String(localAct?.number || ""),
          attachment: uploaded.attachment,
          period: {
            from: String(localAct?.periodFrom || ""),
            to: String(localAct?.periodTo || "")
          },
          actType
        }
      };
    }

    function qualityValues(localItem, qualityActs) {
      const daysPerMonth = Number(localItem?.daysPerMonth);
      const daysCount =
        localItem?.daysCount === null || localItem?.daysCount === undefined
          ? null
          : Number(localItem.daysCount);
      if (
        daysCount !== null &&
        (!Number.isInteger(daysCount) || daysCount < 0 || daysCount > daysPerMonth)
      ) {
        throw new Error("Некорректное количество дней ненадлежащего качества.");
      }

      const hasViolation = qualityActs.some(
        (act) => String(act?.actType || "") === "QUALITY_VIOLATION_ACT"
      );
      if (hasViolation && daysCount === null) {
        throw new Error(
          "Для акта нарушения качества нужно указать количество дней " +
            "ненадлежащего качества."
        );
      }

      const reductionText = qualityDecimalText(
        localItem?.costReduction,
        "Размер уменьшения платы"
      );
      return {
        cost: qualityDecimalText(
          localItem?.cost,
          "Стоимость в ежемесячной плате"
        ),
        daysCount: daysCount === null ? null : String(daysCount),
        daysPerMonth,
        costReduction: reductionText === null ? null : Number(reductionText),
        hasViolation
      };
    }

    async function prepareNewQualityAssessment(localPayload, options = {}) {
      const reportingPeriod = await findReportingPeriod(localPayload);
      const house = await getWorkplanningHouseByFias(localPayload.fiasHouseCode);
      const houseContext = await getHouseInfo(localPayload.houseObjectGuid);
      const orgRootGuid = houseContext.organizationGuid;
      const remote = await searchQualityAssessment(reportingPeriod.guid, false);
      if (!remote?.estimation || !Array.isArray(remote?.estimationList)) {
        throw new Error("ГИС ЖКХ не вернул данные формы оценки качества.");
      }

      const existingGuid = remote?.estimation?.guid || null;
      const knownGuid = localPayload?.knownEstimationGuid || null;
      if (existingGuid) {
        if (!knownGuid || String(existingGuid) !== String(knownGuid)) {
          throw new Error(
            "Для выбранного месяца в ГИС ЖКХ уже существует другая оценка качества."
          );
        }
        throw new Error(
          "Оценка качества уже сохранена в ГИС ЖКХ. " +
            "Для изменения используйте «Разместить изменения»."
        );
      }

      const localItems = Array.isArray(localPayload?.items)
        ? localPayload.items
        : [];
      if (!localItems.length) {
        throw new Error("В оценку качества не включено ни одной работы.");
      }

      const estimationList = [];
      const actMappings = [];
      const seededLocalActIds = new Set();
      const allowMissingActs = Boolean(options?.allowMissingActs);

      for (const localItem of localItems) {
        const remoteItem = matchQualityItem(localItem, remote.estimationList);
        if (!remoteItem) {
          throw new Error(
            "Не удалось сопоставить работу «" +
              String(localItem?.workName || localItem?.workCode || "") +
              "» с формой оценки качества ГИС ЖКХ."
          );
        }

        const localActs = Array.isArray(localItem?.acts) ? localItem.acts : [];
        if (!localActs.length && !allowMissingActs) {
          throw new Error(
            "Для работы «" + String(localItem?.workName || "") + "» не добавлен акт."
          );
        }

        const qualityActs = [];
        for (const localAct of localActs) {
          const localActId = String(localAct?.localActId || "");
          if (localAct?.remoteActGuid) {
            const existingAct = await getQualityActByGuid(
              localAct.remoteActGuid,
              orgRootGuid,
              house.guid
            );
            const normalized = normalizeRemoteQualityAct(existingAct);
            qualityActs.push(normalized);
            actMappings.push({
              localActId: localAct?.localActId || null,
              contentGuid: normalized?.attachment?.contentGuid || null,
              guid: normalized?.guid || null
            });
            continue;
          }

          if (localActId && seededLocalActIds.has(localActId)) {
            continue;
          }

          const created = await createLocalQualityAct(
            localAct,
            orgRootGuid,
            house
          );
          if (localActId) seededLocalActIds.add(localActId);
          qualityActs.push(created.act);
          actMappings.push({
            localActId: created.localActId,
            contentGuid: created.contentGuid,
            guid: null
          });
        }

        if (!qualityActs.length && !allowMissingActs) {
          continue;
        }

        const values = qualityValues(localItem, qualityActs);
        estimationList.push({
          guid: remoteItem?.guid || null,
          work: remoteItem?.work || null,
          workType: remoteItem?.workType || null,
          workName: remoteItem?.workName || localItem?.workName || null,
          cost: values.cost,
          daysCount: values.daysCount,
          daysPerMonth: values.daysPerMonth,
          costReduction: values.costReduction,
          qualityAct: qualityActs,
          hasQualityActs: qualityActs.length > 0,
          hasQualityViolationActs: values.hasViolation
        });
      }

      const estimation = Object.assign({}, remote.estimation, {
        period: {
          month: Number(localPayload.month),
          year: Number(localPayload.year)
        },
        fiasHouseCode: String(localPayload.fiasHouseCode || "")
      });

      return {
        reportingPeriod,
        remote,
        body: {
          estimation,
          estimationList,
          orgRootGuid
        },
        actMappings
      };
    }

    async function prepareExistingQualityAssessment(
      localPayload,
      reportingPeriod,
      remote,
      options = {}
    ) {
      const knownGuid = String(localPayload?.knownEstimationGuid || "");
      const remoteGuid = String(remote?.estimation?.guid || "");
      if (!remoteGuid) {
        throw new Error("Сохранённая оценка качества в ГИС ЖКХ не найдена.");
      }
      if (!knownGuid || knownGuid !== remoteGuid) {
        throw new Error(
          "Оценка качества в ГИС ЖКХ изменилась после последней синхронизации. " +
            "Обновите данные перед повторным изменением."
        );
      }

      const house = await getWorkplanningHouseByFias(localPayload.fiasHouseCode);
      const houseContext = await getHouseInfo(localPayload.houseObjectGuid);
      const orgRootGuid = houseContext.organizationGuid;
      const workList = await getWorkListDetail(localPayload.workListGuid);
      const workListItems = Array.isArray(workList?.workListItems)
        ? workList.workListItems
        : [];
      const remoteItems = Array.isArray(remote?.estimationList)
        ? remote.estimationList
        : [];
      const localItems = Array.isArray(localPayload?.items)
        ? localPayload.items
        : [];
      if (!localItems.length) {
        throw new Error("В оценку качества не включено ни одной работы.");
      }

      const estimationList = [];
      const actMappings = [];
      const seededLocalActIds = new Set();
      const knownRemoteActsByGuid = new Map();
      const knownRemoteActsByContentGuid = new Map();
      const stageSharedNewActs = Boolean(options?.stageSharedNewActs);
      const allowMissingActs = Boolean(options?.allowMissingActs);

      function rememberRemoteAct(act) {
        const normalized = normalizeRemoteQualityAct(act);
        const guid = String(normalized?.guid || "");
        const contentGuid = String(
          normalized?.attachment?.contentGuid || ""
        );
        if (guid) knownRemoteActsByGuid.set(guid, normalized);
        if (contentGuid) {
          knownRemoteActsByContentGuid.set(contentGuid, normalized);
        }
        return normalized;
      }

      for (const localItem of localItems) {
        let remoteItem = matchQualityItem(localItem, remoteItems);
        if (!remoteItem) {
          const sourceItem = findWorkListItem(localItem, workListItems);
          remoteItem = {
            guid: null,
            work: compactReference(sourceItem?.work),
            workType: compactReference(sourceItem?.workType),
            workName:
              sourceItem?.workName ||
              sourceItem?.work?.name ||
              localItem?.workName ||
              null
          };
        }

        const remoteActs = remoteItem?.guid
          ? await searchQualityActs(remoteItem.guid)
          : [];
        for (const remoteAct of remoteActs) {
          rememberRemoteAct(remoteAct);
        }
        const removedActGuids = new Set(
          (localItem?.removedRemoteActGuids || []).map(String)
        );
        const removedContentGuids = new Set(
          (localItem?.removedRemoteContentGuids || []).map(String)
        );
        const localActs = Array.isArray(localItem?.acts) ? localItem.acts : [];

        const claimedRemoteGuids = new Set();
        const claimedContentGuids = new Set();
        for (const localAct of localActs) {
          if (localAct?.remoteActGuid) {
            claimedRemoteGuids.add(String(localAct.remoteActGuid));
          }
          if (localAct?.remoteContentGuid) {
            claimedContentGuids.add(String(localAct.remoteContentGuid));
          }
        }

        const qualityActs = remoteActs
          .filter((act) => {
            const guid = String(act?.guid || "");
            const contentGuid = String(act?.attachment?.contentGuid || "");
            if (guid && removedActGuids.has(guid)) return false;
            if (contentGuid && removedContentGuids.has(contentGuid)) return false;
            if (guid && claimedRemoteGuids.has(guid)) return false;
            if (contentGuid && claimedContentGuids.has(contentGuid)) return false;
            return true;
          })
          .map(normalizeRemoteQualityAct);

        for (const localAct of localActs) {
          const matched = remoteActs.find((remoteAct) =>
            remoteQualityActMatchesLocal(localAct, remoteAct)
          );
          if (matched) {
            const normalized = normalizeRemoteQualityAct(matched);
            qualityActs.push(normalized);
            actMappings.push({
              localActId: localAct?.localActId || null,
              contentGuid: normalized?.attachment?.contentGuid || null,
              guid: normalized?.guid || null
            });
            continue;
          }

          if (localAct?.remoteActGuid || localAct?.remoteContentGuid) {
            const remoteActGuid = String(localAct?.remoteActGuid || "");
            const remoteContentGuid = String(
              localAct?.remoteContentGuid || ""
            );
            let normalized =
              (remoteActGuid &&
                knownRemoteActsByGuid.get(remoteActGuid)) ||
              (remoteContentGuid &&
                knownRemoteActsByContentGuid.get(remoteContentGuid)) ||
              null;

            if (!normalized && remoteActGuid) {
              const existingAct = await getQualityActByGuid(
                remoteActGuid,
                orgRootGuid,
                house.guid
              );
              normalized = rememberRemoteAct(existingAct);
            }

            if (!normalized) {
              throw new Error(
                "Не удалось повторно использовать сохранённый акт «" +
                  String(localAct?.number || "") +
                  "» для работы «" +
                  String(localItem?.workName || "") +
                  "»."
              );
            }

            qualityActs.push(normalized);
            actMappings.push({
              localActId: localAct?.localActId || null,
              contentGuid: normalized?.attachment?.contentGuid || null,
              guid: normalized?.guid || null
            });
            continue;
          }

          const localActId = String(localAct?.localActId || "");
          if (
            stageSharedNewActs &&
            localActId &&
            seededLocalActIds.has(localActId)
          ) {
            continue;
          }

          const created = await createLocalQualityAct(
            localAct,
            orgRootGuid,
            house
          );
          if (stageSharedNewActs && localActId) {
            seededLocalActIds.add(localActId);
          }
          qualityActs.push(created.act);
          actMappings.push({
            localActId: created.localActId,
            contentGuid: created.contentGuid,
            guid: null
          });
        }

        if (!qualityActs.length && !allowMissingActs) {
          if (stageSharedNewActs) {
            continue;
          }
          throw new Error(
            "Для работы «" + String(localItem?.workName || "") + "» не добавлен акт."
          );
        }

        const values = qualityValues(localItem, qualityActs);
        estimationList.push({
          guid: remoteItem.guid,
          work: remoteItem?.work || null,
          workType: remoteItem?.workType || null,
          workName: remoteItem?.workName || localItem?.workName || null,
          cost: values.cost,
          daysCount: values.daysCount,
          daysPerMonth: values.daysPerMonth,
          costReduction: values.costReduction,
          qualityAct: qualityActs,
          hasQualityActs: qualityActs.length > 0,
          hasQualityViolationActs: values.hasViolation
        });
      }

      const estimation = Object.assign({}, remote.estimation, {
        status: "DRAFT",
        period: {
          month: Number(localPayload.month),
          year: Number(localPayload.year)
        },
        fiasHouseCode: String(localPayload.fiasHouseCode || "")
      });

      return {
        reportingPeriod,
        remote,
        body: {
          estimation,
          estimationList,
          orgRootGuid
        },
        actMappings
      };
    }

    function qualityResult(remote, reportingPeriod, localItems, actMappings) {
      const remoteItems = Array.isArray(remote?.estimationList)
        ? remote.estimationList
        : [];
      const items = (localItems || []).map((localItem) => {
        const matched = matchQualityItem(localItem, remoteItems);
        return {
          localItemId: localItem?.localItemId || null,
          guid: matched?.guid || null
        };
      });
      return {
        reportingPeriodGuid: reportingPeriod?.guid || null,
        estimationGuid: remote?.estimation?.guid || null,
        gisStatus:
          remote?.estimation?.status ||
          remote?.estimation?.dbStatus ||
          null,
        items,
        acts: actMappings || []
      };
    }

    async function qualityResultWithActs(
      remote,
      reportingPeriod,
      localItems,
      fallbackMappings = []
    ) {
      const result = qualityResult(
        remote,
        reportingPeriod,
        localItems,
        fallbackMappings
      );
      const mappings = [];
      const remoteItems = Array.isArray(remote?.estimationList)
        ? remote.estimationList
        : [];

      for (const localItem of localItems || []) {
        const matchedItem = matchQualityItem(localItem, remoteItems);
        if (!matchedItem?.guid) continue;
        const remoteActs = await searchQualityActs(matchedItem.guid);
        for (const localAct of localItem?.acts || []) {
          let candidates = remoteActs.filter((remoteAct) =>
            remoteQualityActMatchesLocal(localAct, remoteAct)
          );
          if (!candidates.length && !localAct?.remoteActGuid && !localAct?.remoteContentGuid) {
            // Metadata is only a fallback for a newly created, unlinked act.
            candidates = remoteActs.filter((remoteAct) =>
              qualityActMetadataMatchesLocal(localAct, remoteAct)
            );
          }
          if (candidates.length > 1) {
            throw new Error(
              "Не удалось однозначно сопоставить акт № " + String(localAct?.number || "") +
              " от " + String(localAct?.regDate || "") +
              ": ГИС ЖКХ вернул несколько подходящих документов. " +
              "Загрузите оценку из ГИС и проверьте акт перед повторной отправкой."
            );
          }
          const matchedAct = candidates[0] || null;
          if (!matchedAct) continue;
          mappings.push({
            localActId: localAct?.localActId || null,
            contentGuid: matchedAct?.attachment?.contentGuid || null,
            guid: matchedAct?.guid || null
          });
        }
      }
      if (mappings.length) {
        // Keep each uploaded file's safe fallback when the fresh response has
        // not yet returned enough metadata to identify that particular act.
        const byLocalId = new Map(
          (fallbackMappings || []).map((mapping) => [String(mapping.localActId), mapping])
        );
        for (const mapping of mappings) {
          byLocalId.set(String(mapping.localActId), mapping);
        }
        result.acts = Array.from(byLocalId.values());
      }
      return result;
    }

    function hasSharedNewQualityAct(localPayload) {
      const counts = new Map();
      for (const item of localPayload?.items || []) {
        for (const act of item?.acts || []) {
          if (act?.remoteActGuid) continue;
          const key = String(act?.localActId || "");
          if (!key) continue;
          counts.set(key, (counts.get(key) || 0) + 1);
        }
      }
      return Array.from(counts.values()).some((count) => count > 1);
    }

    async function saveNewQualityAssessment(localPayload, options = {}) {
      const prepared = await prepareNewQualityAssessment(localPayload, options);
      await request(
        "POST",
        "/workplanning/api/rest/services/save/estimation",
        prepared.body,
        STATE_WORKPLANNING
      );
      let fresh = await searchQualityAssessment(
        prepared.reportingPeriod.guid,
        false
      );
      let result = await qualityResultWithActs(
        fresh,
        prepared.reportingPeriod,
        localPayload.items,
        prepared.actMappings
      );

      const hasSharedNewAct = hasSharedNewQualityAct(localPayload);

      if (hasSharedNewAct) {
        const effectivePayload = mergeSavedActMappings(
          localPayload,
          result.acts
        );
        effectivePayload.knownEstimationGuid =
          result.estimationGuid || fresh?.estimation?.guid || null;

        const linked = await prepareExistingQualityAssessment(
          effectivePayload,
          prepared.reportingPeriod,
          fresh,
          options
        );
        await request(
          "POST",
          "/workplanning/api/rest/services/save/estimation",
          linked.body,
          STATE_WORKPLANNING
        );
        fresh = await searchQualityAssessment(
          prepared.reportingPeriod.guid,
          false
        );
        result = await qualityResultWithActs(
          fresh,
          prepared.reportingPeriod,
          effectivePayload.items,
          linked.actMappings
        );
      }

      return {
        prepared,
        fresh,
        result
      };
    }

    async function saveQualityAssessment(localPayload) {
      const reportingPeriod = await findReportingPeriod(localPayload);
      let remote = await searchQualityAssessment(reportingPeriod.guid, false);

      if (!remote?.estimation?.guid) {
        return saveNewQualityAssessment(localPayload, {
          allowMissingActs: true
        });
      }

      const hasSharedNewAct = hasSharedNewQualityAct(localPayload);
      let effectivePayload = localPayload;
      let prepared = await prepareExistingQualityAssessment(
        effectivePayload,
        reportingPeriod,
        remote,
        {
          stageSharedNewActs: hasSharedNewAct,
          allowMissingActs: true
        }
      );
      await request(
        "POST",
        "/workplanning/api/rest/services/save/estimation",
        prepared.body,
        STATE_WORKPLANNING
      );
      let fresh = await searchQualityAssessment(reportingPeriod.guid, false);
      let result = await qualityResultWithActs(
        fresh,
        reportingPeriod,
        effectivePayload.items,
        prepared.actMappings
      );

      if (hasSharedNewAct) {
        effectivePayload = mergeSavedActMappings(
          localPayload,
          result.acts
        );
        effectivePayload.knownEstimationGuid =
          result.estimationGuid || fresh?.estimation?.guid || null;
        remote = fresh;

        prepared = await prepareExistingQualityAssessment(
          effectivePayload,
          reportingPeriod,
          remote,
          { allowMissingActs: true }
        );
        await request(
          "POST",
          "/workplanning/api/rest/services/save/estimation",
          prepared.body,
          STATE_WORKPLANNING
        );
        fresh = await searchQualityAssessment(reportingPeriod.guid, false);
        result = await qualityResultWithActs(
          fresh,
          reportingPeriod,
          effectivePayload.items,
          prepared.actMappings
        );
      }

      return {
        prepared,
        fresh,
        result
      };
    }

    function mergeSavedActMappings(localPayload, mappings) {
      const byId = new Map(
        (mappings || [])
          .filter((item) => item?.localActId)
          .map((item) => [String(item.localActId), item])
      );
      return Object.assign({}, localPayload, {
        items: (localPayload?.items || []).map((item) =>
          Object.assign({}, item, {
            acts: (item?.acts || []).map((act) => {
              const mapping = byId.get(String(act?.localActId || ""));
              if (!mapping) return act;
              return Object.assign({}, act, {
                remoteContentGuid:
                  mapping.contentGuid || act?.remoteContentGuid || null,
                remoteActGuid: mapping.guid || act?.remoteActGuid || null
              });
            })
          })
        )
      });
    }

    async function approveQualityAssessment(localPayload) {
      const reportingPeriod = await findReportingPeriod(localPayload);
      let remote = await searchQualityAssessment(reportingPeriod.guid, false);
      let effectivePayload = localPayload;

      if (!remote?.estimation?.guid) {
        const saved = await saveNewQualityAssessment(localPayload, {
          allowMissingActs: false
        });
        remote = saved.fresh;
        effectivePayload = mergeSavedActMappings(localPayload, saved.result.acts);
        effectivePayload.knownEstimationGuid =
          saved.result.estimationGuid || remote?.estimation?.guid || null;
      } else if (hasSharedNewQualityAct(localPayload)) {
        const saved = await saveQualityAssessment(localPayload);
        remote = saved.fresh;
        effectivePayload = mergeSavedActMappings(localPayload, saved.result.acts);
        effectivePayload.knownEstimationGuid =
          saved.result.estimationGuid || remote?.estimation?.guid || null;
      }

      const prepared = await prepareExistingQualityAssessment(
        effectivePayload,
        reportingPeriod,
        remote
      );

      await request(
        "POST",
        "/workplanning/api/rest/services/approve/estimation",
        prepared.body,
        STATE_WORKPLANNING
      );
      const fresh = await searchQualityAssessment(reportingPeriod.guid, false);
      const result = await qualityResultWithActs(
        fresh,
        reportingPeriod,
        effectivePayload.items,
        prepared.actMappings
      );
      result.gisStatus = "APPROVED";
      return result;
    }

    async function cancelQualityAssessment(localPayload) {
      const reportingPeriod = await findReportingPeriod(localPayload);
      await request(
        "PUT",
        "/workplanning/api/rest/services/cancel/estimation",
        { workReportingPeriodGuid: reportingPeriod.guid },
        STATE_WORKPLANNING
      );
      return {
        cancelled: true,
        reportingPeriodGuid: reportingPeriod.guid,
        estimationGuid: null,
        gisStatus: "CANCELLED",
        items: [],
        acts: []
      };
    }

    if (command.type === "resolve_planned_work_units") {
      return {
        ok: true,
        result: await resolvePlannedWorkUnits(payload)
      };
    }

    if (command.type === "list_organization_work_options") {
      return {
        ok: true,
        result: { items: await listOrganizationWorkOptions() }
      };
    }

    if (command.type === "list_unplanned_work_options") {
      return {
        ok: true,
        result: {
          items: await listUnplannedWorkOptions(payload)
        }
      };
    }


    if (command.type === "load_quality_assessment") {
      return {
        ok: true,
        result: await loadQualityAssessment(payload)
      };
    }

    if (command.type === "save_quality_assessment") {
      const saved = await saveQualityAssessment(payload);
      return { ok: true, result: saved.result };
    }

    if (command.type === "approve_quality_assessment") {
      return {
        ok: true,
        result: await approveQualityAssessment(payload)
      };
    }

    if (command.type === "cancel_quality_assessment") {
      return {
        ok: true,
        result: await cancelQualityAssessment(payload)
      };
    }

    if (command.type === "load_work_fixation") {
      return {
        ok: true,
        result: await loadWorkFixation(payload)
      };
    }

    if (command.type === "save_work_fixation") {
      const reportingPeriod = await findReportingPeriod(payload);
      const remoteData = await searchFixedWorks(reportingPeriod.guid);
      const hadExisting = (remoteData?.plannedWorkList || []).some(
        (item) => Boolean(item?.guid)
      );
      const prepared = await buildFixedWorksSavePayload(
        payload,
        reportingPeriod,
        remoteData
      );

      const saved = await request(
        "PUT",
        "/workplanning/api/rest/services/save/fixedworks",
        prepared.body,
        STATE_WORKPLANNING
      );
      const savedItems = Array.isArray(saved?.plannedWorkList)
        ? saved.plannedWorkList
        : [];
      const savedUnplanned = Array.isArray(saved?.unplannedWorkList)
        ? saved.unplannedWorkList
        : [];
      const fixedItems = prepared.plannedMappings.map((item) => ({
        localItemId: item.localItemId,
        guid: savedItems[item.remoteIndex]?.guid || null
      }));
      const unplannedItems = prepared.unplannedMappings.map((item) => {
        const matched =
          savedUnplanned.find(
            (row) =>
              item.existingGuid &&
              String(row?.guid || "") === String(item.existingGuid)
          ) ||
          savedUnplanned.find(
            (row) =>
              String(row?.work?.guid || "") === String(item.workGuid || "")
          ) ||
          null;
        return {
          localItemId: item.localItemId,
          guid: matched?.guid || null,
          workTypeCode: item.workTypeCode || matched?.workType?.code || null,
          workTypeGuid: item.workTypeGuid || matched?.workType?.guid || null,
          workTypeName:
            item.workTypeName ||
            matched?.workType?.serviceName ||
            matched?.work?.serviceType?.serviceName ||
            null,
          unitName:
            item.unitName ||
            matched?.work?.dimensionsUnit?.dimensionsUnitOkei?.nationalSymbolName ||
            matched?.work?.dimensionsUnit?.dimensionsUnitString ||
            null
        };
      });

      const photoResults = [];
      function collectPhotoResults(source) {
        for (const item of source || []) {
          for (const photo of item?.photos || []) {
            const contentGuid = photo?.attachment?.contentGuid || null;
            if (!contentGuid) continue;
            const mapping = prepared.photoMappings.find(
              (entry) => String(entry.contentGuid || "") === String(contentGuid)
            );
            if (mapping?.localPhotoId) {
              photoResults.push({
                localPhotoId: mapping.localPhotoId,
                contentGuid,
                photoGuid: photo?.guid || photo?.attachment?.guid || null
              });
            }
          }
        }
      }
      collectPhotoResults(savedItems);
      collectPhotoResults(savedUnplanned);

      return {
        ok: true,
        result: {
          reportingPeriodGuid: reportingPeriod.guid,
          workListGuid: reportingPeriod.workListGuid || payload.workListGuid,
          year: Number(reportingPeriod?.period?.year ?? payload.year),
          month: Number(reportingPeriod?.period?.month ?? payload.month),
          hadExisting,
          fixedItems,
          unplannedItems,
          photoResults
        }
      };
    }

    if (command.type === "load_work_plan") {
      const plan = await searchWorkPlan(payload.workListGuid, payload.year);
      return {
        ok: true,
        result: normalizeWorkPlanForImport(plan)
      };
    }

    if (command.type === "save_work_plan") {
      const workList = await getWorkListDetail(payload.workListGuid);
      const existingPlan = await searchWorkPlan(workList.guid, payload.year);
      const body = saveWorkPlanPayload(payload, workList, existingPlan);

      const saved = await request(
        "PUT",
        "/workplanning/api/rest/services/save/workplan",
        body,
        STATE_WORKPLANNING
      );
      if (!saved?.guid) {
        throw new Error("ГИС ЖКХ не вернул GUID сохраненного плана работ и услуг.");
      }

      return {
        ok: true,
        result: {
          found: true,
          gisStatus: "SAVED",
          workPlanGuid: saved.guid,
          workListGuid: saved.workListGuid || workList.guid,
          year: Number(saved.year ?? payload.year),
          lastUpdateDate: saved.lastUpdateDate || null,
          created: !existingPlan?.guid
        }
      };
    }

    if (command.type === "approve_work_list") {
      const draft = await searchExactWorkList(payload, ["DRAFT"]);
      if (!draft) {
        const existing = await searchExactWorkList(payload, null);
        if (existing?.status === "APPROVED") {
          return { ok: true, result: statusResult(existing) };
        }
        throw new Error(
          "Проект перечня с таким домом и периодом не найден в ГИС ЖКХ."
        );
      }

      const approvalWorkList = approvalPayloadFromDraft(draft);

      await request(
        "PUT",
        "/workplanning/api/rest/services/approve/worklist",
        { workList: approvalWorkList },
        STATE_WORKPLANNING
      );

      await sleep(900);
      const updated = await searchExactWorkList(payload, null);
      if (updated) {
        return { ok: true, result: statusResult(updated) };
      }

      return {
        ok: true,
        result: statusResult(draft, {
          gisStatus: "APPROVED",
          wasApproved: true
        })
      };
    }

    throw new Error("Неизвестная команда интеграции: " + String(command.type));
  } catch (error) {
    return { ok: false, error: readableError(error) };
  }
}
