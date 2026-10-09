"""Organization routing uses snapshots and a gateway with no live sockets."""

import os
import json
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QWidget  # noqa: E402

from gis_jkh_assistant.app.gis_integration_worker import GisIntegrationWorker  # noqa: E402
from gis_jkh_assistant.database import Repository  # noqa: E402
from gis_jkh_assistant.gis_integration import (  # noqa: E402
    IntegrationError, IntegrationStatus, LocalIntegrationGateway,
)
from gis_jkh_assistant.gis_integration.organization_context import (  # noqa: E402
    helper_supports_organization_routing, with_organization_context,
)


@pytest.mark.parametrize("version,supported", [
    (None, False), ("unknown", False), ("0.1.29", False), ("0.1.30", True),
    ("0.1.31", True), ("1.0.0", True),
])
def test_routing_requires_a_helper_that_understands_organization_context(version, supported):
    assert helper_supports_organization_routing(version) is supported


def test_context_captures_the_selected_organization_before_a_queued_worker_runs(tmp_path):
    app = QApplication.instance() or QApplication([])
    repository = Repository(tmp_path / "routing.db")
    org_a = repository.create_organization(name="УО А", ogrn="1022600000000")
    org_b = repository.create_organization(name="УО Б", ogrn="1022600000001")
    repository.update_organization(org_a.id, gis_organization_root_guid="root-a")
    page = QWidget()
    page.repository = repository
    page.organization_combo = QComboBox(page)
    for organization in (org_a, org_b):
        page.organization_combo.addItem(organization.name, organization.id)
    payload = {"workListGuid": "list-a", "month": 7, "year": 2020}
    worker = GisIntegrationWorker(SimpleNamespace(), "save_work_fixation", payload, parent=page)
    page.organization_combo.setCurrentIndex(1)
    assert worker.payload["organizationContext"] == {
        "ogrn": org_a.ogrn, "organizationRootGuid": "root-a",
    }
    assert payload == {"workListGuid": "list-a", "month": 7, "year": 2020}
    second = GisIntegrationWorker(SimpleNamespace(), "save_work_fixation", payload, parent=page)
    assert second.payload["organizationContext"] == {"ogrn": org_b.ogrn}
    importer = GisIntegrationWorker(SimpleNamespace(), "get_current_organization", {}, parent=page)
    assert importer.payload == {}, "Import discovers the cabinet before choosing a local organization"
    page.deleteLater()
    app.processEvents()
    repository.close()


def test_missing_local_organization_cannot_create_an_unscoped_command(tmp_path):
    repository = Repository(tmp_path / "missing.db")
    with pytest.raises(IntegrationError, match="Выберите управляющую организацию"):
        with_organization_context(repository, None, {})
    repository.close()


@pytest.mark.parametrize("version", [None, "0.1.27", "0.1.29"])
def test_old_helper_is_rejected_before_a_scoped_command_enters_the_queue(version):
    gateway = LocalIntegrationGateway()
    gateway._server = object()
    gateway.status = lambda: IntegrationStatus(True, True, True, helper_version=version)
    with pytest.raises(IntegrationError, match=r"0\.1\.30"):
        gateway.execute("approve_work_list", {"organizationContext": {"ogrn": "1022600000000"}})
    assert gateway._state.commands.empty()
    assert gateway._state.results == {}


def test_gateway_preserves_organization_context_in_the_command_sent_to_a_new_helper():
    gateway = LocalIntegrationGateway()
    gateway._server = object()
    gateway.status = lambda: IntegrationStatus(True, True, True, helper_version="0.1.30")
    received = []

    def respond():
        command = gateway._state.commands.get(timeout=2)
        received.append(command)
        gateway._state.results[command["id"]].put({"ok": True, "result": {"saved": True}})

    thread = threading.Thread(target=respond)
    thread.start()
    payload = {"organizationContext": {"ogrn": "1022600000001", "organizationRootGuid": "root-b"}}
    result = gateway.execute("save_work_plan", payload, timeout=2)
    thread.join(timeout=2)
    assert result == {"saved": True}
    assert received[0]["payload"] == payload
    assert gateway._state.results == {}


def test_http_poll_from_an_older_helper_cannot_consume_a_new_command():
    gateway = LocalIntegrationGateway(port=0)
    gateway.start()
    try:
        assert gateway._server is not None
        command = {"id": "isolated-command", "type": "save_work_plan",
                   "payload": {"organizationContext": {"ogrn": "1022600000000"}}}
        gateway._state.commands.put(command)
        url = f"http://127.0.0.1:{gateway._server.server_port}/api/integration/next"
        for version in (None, "0.1.29", "0.1.30"):
            headers = {"X-GIS-JKH-Helper": "desktop-helper"}
            if version:
                headers["X-GIS-JKH-Helper-Version"] = version
            with urlopen(Request(url, headers=headers), timeout=2) as response:
                reply = json.load(response)
            if version != "0.1.30":
                assert reply["idle"] is True
                assert gateway._state.commands.qsize() == 1
            else:
                assert reply == command
                assert gateway._state.commands.empty()
    finally:
        gateway.stop()
