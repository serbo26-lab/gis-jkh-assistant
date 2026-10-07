const LOCAL_BASE = "http://127.0.0.1:8765/api/integration";
const HELPER_HEADER = { "X-GIS-JKH-Helper": "desktop-helper" };
const GIS_MATCH = "https://lk.dom.gosuslugi.ru/*";
const KEEP_ALIVE_INTERVAL_MS = 4 * 60 * 1000;
const KEEP_ALIVE_ALARM = "gis-jkh-session-keepalive";
const HELPER_CODE_VERSION = "0.1.23";

let lastKeepAliveAttemptMs = 0;
let lastKeepAliveSuccessMs = null;
let keepAliveError = null;

async function localFetch(path, options = {}) {
  const headers = Object.assign({}, HELPER_HEADER, options.headers || {});
  return fetch(LOCAL_BASE + path, Object.assign({}, options, { headers }));
}

async function findGisTab() {
  const tabs = await chrome.tabs.query({ url: GIS_MATCH });
  if (!tabs.length) return null;

  const active = tabs.find((tab) => tab.active);
  return active || tabs[0];
}

async function hello() {
  try {
    const tab = await findGisTab();
    const response = await localFetch("/hello", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gisPageReady: Boolean(tab && tab.id),
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
  const now = Date.now();
  if (now - lastKeepAliveAttemptMs < KEEP_ALIVE_INTERVAL_MS) {
    return;
  }

  const tab = await findGisTab();
  if (!tab?.id) {
    return;
  }

  lastKeepAliveAttemptMs = now;
  try {
    await executeInGisTab({ type: "keep_alive", payload: {} });
    lastKeepAliveSuccessMs = Date.now();
    keepAliveError = null;
  } catch (error) {
    keepAliveError = String(error?.message || error);
  }
}

async function executeInGisTab(command) {
  const tab = await findGisTab();
  if (!tab?.id) {
    throw new Error("Не найдена открытая вкладка личного кабинета ГИС ЖКХ.");
  }

  // Long registry operations can take several minutes. Keep the desktop-side
  // connection status fresh while the command is still executing in the GIS tab.
  const heartbeat = setInterval(() => {
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
    return result.result || {};
  } finally {
    clearInterval(heartbeat);
    if (progressProbe) clearInterval(progressProbe);
    await hello();
  }
}

let reconnectDelayMs = 1000;

async function pollLoop() {
  const connected = await hello();

  if (!connected) {
    // Do not also call /next when the desktop app is closed. One failed probe is
    // enough, and a short backoff keeps the extension console/network log quiet.
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, 10000);
    setTimeout(pollLoop, reconnectDelayMs);
    return;
  }

  reconnectDelayMs = 1000;

  await maybeKeepSessionAlive();

  try {
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
  }

  setTimeout(pollLoop, reconnectDelayMs);
}

pollLoop();

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

    window.__GIS_JKH_ASSISTANT_PROGRESS = null;

    function setProgress(progress) {
      window.__GIS_JKH_ASSISTANT_PROGRESS = progress || null;
    }

    function sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function request(method, url, data = undefined, stateGuid = STATE_WORKPLANNING) {
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
      const response = await $http(config);
      return response.data;
    }

    async function requestBinary(method, url, stateGuid = null) {
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
          !["NEW", "CREATED", "PROCESSING", "IN_PROCESS"].includes(
            String(item.status).toUpperCase()
          ) &&
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

    async function getWorkListDetail(workListGuid) {
      const guid = String(workListGuid || "").trim();
      if (!guid) {
        throw new Error("Не передан GUID размещенного перечня ГИС ЖКХ.");
      }

      const data = await request(
        "GET",
        "/workplanning/api/rest/services/search/worklist/" + encodeURIComponent(guid),
        undefined,
        STATE_WORKPLANNING
      );
      if (!data?.guid) {
        throw new Error("Размещенный перечень не найден в ГИС ЖКХ.");
      }
      if (data.status !== "APPROVED") {
        throw new Error(
          "Перечень больше не находится в статусе «Размещен» в ГИС ЖКХ."
        );
      }
      return data;
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

      await request(
        "GET",
        "/auth/api/rest/services/auth/echo/",
        undefined,
        STATE_WORKPLANNING
      );

      // If the warning dialog appeared between keepalive cycles, use its native
      // continuation action as a recovery path.
      const continueButton = Array.from(document.querySelectorAll("button")).find(
        (button) => (button.textContent || "").trim() === "Продолжить работу"
      );
      if (continueButton) {
        continueButton.click();
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
      const item = await searchExactWorkList(payload, null);
      return { ok: true, result: statusResult(item) };
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
      if (localRemoteGuid && remoteGuid === localRemoteGuid) return true;
      if (localContentGuid && contentGuid === localContentGuid) return true;
      return false;
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

    async function prepareNewQualityAssessment(localPayload) {
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
        if (!localActs.length) {
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

        if (!qualityActs.length) {
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

        if (!qualityActs.length) {
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
          let matchedAct = remoteActs.find((remoteAct) =>
            remoteQualityActMatchesLocal(localAct, remoteAct)
          );
          if (!matchedAct) {
            matchedAct =
              remoteActs.find(
                (remoteAct) =>
                  String(remoteAct?.number || "") ===
                    String(localAct?.number || "") &&
                  String(remoteAct?.actType || "") ===
                    String(localAct?.actType || "") &&
                  String(remoteAct?.period?.from || "") ===
                    String(localAct?.periodFrom || "") &&
                  String(remoteAct?.period?.to || "") ===
                    String(localAct?.periodTo || "")
              ) || null;
          }
          if (!matchedAct) continue;
          mappings.push({
            localActId: localAct?.localActId || null,
            contentGuid: matchedAct?.attachment?.contentGuid || null,
            guid: matchedAct?.guid || null
          });
        }
      }
      if (mappings.length) result.acts = mappings;
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

    async function saveNewQualityAssessment(localPayload) {
      const prepared = await prepareNewQualityAssessment(localPayload);
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
          fresh
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
        return saveNewQualityAssessment(localPayload);
      }

      const hasSharedNewAct = hasSharedNewQualityAct(localPayload);
      let effectivePayload = localPayload;
      let prepared = await prepareExistingQualityAssessment(
        effectivePayload,
        reportingPeriod,
        remote,
        { stageSharedNewActs: hasSharedNewAct }
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
          remote
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
        const saved = await saveNewQualityAssessment(localPayload);
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
