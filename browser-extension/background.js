const LOCAL_BASE = "http://127.0.0.1:8765/api/integration";
const HELPER_HEADER = { "X-GIS-JKH-Helper": "desktop-helper" };
const GIS_MATCH = "https://lk.dom.gosuslugi.ru/*";

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
        helperVersion: chrome.runtime.getManifest().version
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

  try {
    const response = await localFetch("/next");
    if (response.status === 200) {
      const command = await response.json();
      try {
        const result = await executeInGisTab(command);
        await sendResult(command.id, true, result, null);
      } catch (error) {
        await sendResult(command.id, false, null, String(error?.message || error));
      }
    }
  } catch (_) {
    reconnectDelayMs = 2000;
  }

  setTimeout(pollLoop, reconnectDelayMs);
}

pollLoop();


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

    const payload = command?.payload || {};

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
