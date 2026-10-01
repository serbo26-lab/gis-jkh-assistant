const LOCAL_BASE = "http://127.0.0.1:8765/api/integration";
const HELPER_HEADER = { "X-GIS-JKH-Helper": "desktop-helper" };
const GIS_MATCH = "https://lk.dom.gosuslugi.ru/*";
const KEEP_ALIVE_INTERVAL_MS = 8 * 60 * 1000;
const KEEP_ALIVE_ALARM = "gis-jkh-session-keepalive";

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
        helperVersion: chrome.runtime.getManifest().version,
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
  delayInMinutes: 8,
  periodInMinutes: 8
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== KEEP_ALIVE_ALARM) return;

  const connected = await hello();
  if (connected) {
    await maybeKeepSessionAlive();
  }
});


async function runGisCommand(command) {
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

    function sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function request(method, url, data = undefined, stateGuid = STATE_WORKPLANNING) {
      const config = {
        method,
        url,
        headers: {
          "State-GUID": stateGuid
        }
      };
      if (data !== undefined) {
        config.data = data;
        config.headers["Content-Type"] = "application/json;charset=UTF-8";
      }
      const response = await $http(config);
      return response.data;
    }

    function normalizeError(error) {
      const data = error?.data;
      if (typeof data === "string" && data.trim()) return data.trim();
      if (data && typeof data.message === "string" && data.message.trim()) {
        return data.message.trim();
      }
      if (typeof error?.statusText === "string" && error.statusText.trim()) {
        return error.statusText.trim();
      }
      return String(error?.message || error || "Неизвестная ошибка ГИС ЖКХ.");
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
      return {
        attachment: {
          createDate: gisCreateDate(uploaded.createDate),
          fileName: uploaded.fileName || fileName,
          description: uploaded.description || description,
          contentType: uploaded.contentType || contentType,
          fileSize: Number(uploaded.fileSize ?? size),
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
        return {
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
        const prepared = await localPhotoForSave(photo);
        localPhotos.push(prepared);
        mappings.push({
          localPhotoId: photo?.localPhotoId || null,
          contentGuid: prepared?.attachment?.contentGuid || null
        });
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
        throw new Error("Выбранная внеплановая работа больше не найдена в справочнике.");
      }
      return item;
    }

    async function listUnplannedWorkOptions(payload) {
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
      const plannedGuids = new Set(
        (payload?.plannedWorkGuids || []).filter(Boolean).map(String)
      );
      const plannedCodes = new Set(
        (payload?.plannedWorkCodes || []).filter(Boolean).map(String)
      );
      return (Array.isArray(roots) ? roots : [])
        .filter((item) => {
          const guid = String(item?.guid || "");
          const code = String(item?.code || item?.fieldValue1 || "");
          return !plannedGuids.has(guid) && !plannedCodes.has(code);
        })
        .map((item) => ({
          guid: item?.guid || null,
          code: String(item?.code || item?.fieldValue1 || ""),
          name: item?.fieldValue2 || "",
          workTypeName: item?.fieldValue3 || "",
          unitName: item?.fieldValue4 || ""
        }))
        .filter((item) => item.guid && item.code && item.name);
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
        const workType = compactReference(work?.serviceType) || {
          code: localItem?.workTypeCode || null,
          guid: localItem?.workTypeGuid || null
        };

        const unplannedRow = {
          work: { code: work.code, guid: work.guid },
          workType,
          monthlyFixedWork:
            factCount === null
              ? null
              : {
                  count: factCount,
                  monthYear: {
                    month: Number(localPayload.month),
                    year: Number(localPayload.year)
                  },
                  dates: Array.isArray(existing?.monthlyFixedWork?.dates)
                    ? existing.monthlyFixedWork.dates
                    : []
                },
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
        newUnplanned.push(unplannedRow);
        unplannedMappings.push({
          localItemId: localItem?.localItemId || null,
          resultIndex: preservedUnplanned.length + newUnplanned.length - 1
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
      await request(
        "GET",
        "/auth/api/rest/services/auth/echo/",
        undefined,
        STATE_WORKPLANNING
      );

      // Fallback for a timeout dialog that may already be visible, for example
      // after the computer woke from sleep. The exact GIS ЖКХ button simply
      // performs the same session continuation action.
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

    if (command.type === "list_unplanned_work_options") {
      return {
        ok: true,
        result: {
          items: await listUnplannedWorkOptions(payload)
        }
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
      const unplannedItems = prepared.unplannedMappings.map((item) => ({
        localItemId: item.localItemId,
        guid: savedUnplanned[item.resultIndex]?.guid || null
      }));

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
    let message = String(error?.message || error);
    try {
      const data = error?.data;
      if (typeof data === "string" && data.trim()) message = data.trim();
      if (data && typeof data.message === "string" && data.message.trim()) {
        message = data.message.trim();
      }
    } catch (_) {
      // Keep the original message.
    }
    return { ok: false, error: message };
  }
}
