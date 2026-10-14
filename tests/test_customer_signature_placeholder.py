"""Optional customer stamp: persistence, real PDF layout and isolated Qt controls."""
import base64

import pytest
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

import test_ui_workflow as workflows
from test_standard_act_requisites import source as source_fixture
from gis_jkh_assistant.acceptance_act_details import resolve_act_details
from gis_jkh_assistant.acceptance_act_pdf import AcceptanceActWorkRow, build_acceptance_act_pdf
from gis_jkh_assistant.app.act_requisites_dialog import ActRequisitesDialog
from gis_jkh_assistant.database import Repository
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore

source = source_fixture
desktop = workflows.desktop
qt_app = workflows.qt_app


def test_legacy_options_upgrade_preserves_images_and_defaults_to_signature_line(source):
    repo, work_list = source
    organization_id = repo.get_house(work_list.house_id).organization_id
    people_before = ActRequisitesStore(repo).list_people(organization_id=organization_id)
    # Recreate precisely the options table written before this feature.
    repo.connection.executescript("""
        DROP TABLE act_organization_options;
        CREATE TABLE act_organization_options (
            organization_id TEXT PRIMARY KEY REFERENCES management_organizations(id) ON DELETE CASCADE,
            insert_images INTEGER NOT NULL DEFAULT 0, stamp BLOB, stamp_name TEXT
        );
    """)
    repo.connection.execute("INSERT INTO act_organization_options VALUES (?, 1, ?, ?)",
                            (organization_id, b"SAVED STAMP", "stamp.png"))
    repo.connection.commit()
    repo.close()
    reopened = Repository(repo.database_path)
    try:
        store = ActRequisitesStore(reopened)
        options = store.options(organization_id)
        assert options["customer_signature_placeholder"] == 0
        assert options["insert_images"] == 1
        assert options["stamp"] == b"SAVED STAMP"
        assert options["stamp_name"] == "stamp.png"
        assert store.list_people(organization_id=organization_id) == people_before
        assert not resolve_act_details(reopened, work_list.house_id, "31.01.2026")["customer_signature_placeholder"]
    finally:
        reopened.close()


@pytest.mark.parametrize("images", [0, 1])
def test_option_is_persistent_scoped_to_uo_and_independent_of_executor_images(source, images):
    repo, work_list = source
    organization_id = repo.get_house(work_list.house_id).organization_id
    other = repo.create_organization(name="Другая тестовая УО", ogrn="1022600000540")
    store = ActRequisitesStore(repo)
    assert store.options(organization_id)["customer_signature_placeholder"] == 0
    store.save_options(organization_id, insert_images=images, stamp=b"STAMP", stamp_name="stamp.png")
    store.save_options(organization_id, customer_signature_placeholder=1)
    with_other_connection = Repository(repo.database_path)
    try:
        assert ActRequisitesStore(with_other_connection).options(organization_id)["customer_signature_placeholder"] == 1
    finally:
        with_other_connection.close()
    assert resolve_act_details(repo, work_list.house_id, "31.01.2026")["customer_signature_placeholder"] is True
    assert store.options(other.id)["customer_signature_placeholder"] == 0
    store.save_options(organization_id, stamp_name="updated.png")
    assert store.options(organization_id)["customer_signature_placeholder"] == 1
    store.save_options(organization_id, customer_signature_placeholder=0)
    assert not resolve_act_details(repo, work_list.house_id, "31.01.2026")["customer_signature_placeholder"]
    assert store.options(organization_id)["insert_images"] == images
    assert store.options(organization_id)["stamp"] == b"STAMP"


@pytest.mark.parametrize("field", ["insert_images", "customer_signature_placeholder"])
def test_option_checkbox_saves_by_keyboard_and_rolls_back_on_database_error(desktop, monkeypatch, field):
    repo, organization_id = desktop.repository, desktop.organization.id
    dialog = ActRequisitesDialog(repo, organization_id=organization_id)
    dialog.show()
    workflows._settle(desktop.app)
    checkbox = getattr(dialog, field)
    assert not checkbox.isChecked()
    checkbox.setFocus()
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert checkbox.isChecked()
    assert ActRequisitesStore(repo).options(organization_id)[field] == 1
    reopened = ActRequisitesDialog(repo, organization_id=organization_id)
    assert getattr(reopened, field).isChecked()
    reopened.close()
    reopened.deleteLater()
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: notices.append(args[2]))
    repo.connection.execute("""CREATE TRIGGER fail_option BEFORE UPDATE ON act_organization_options
        BEGIN SELECT RAISE(ABORT, 'TEST OPTION FAILURE'); END""")
    repo.connection.commit()
    QTest.keyClick(checkbox, Qt.Key.Key_Space)
    assert checkbox.isChecked()
    assert ActRequisitesStore(repo).options(organization_id)[field] == 1
    assert notices == ["TEST OPTION FAILURE"]
    dialog.close()
    dialog.deleteLater()


def _test_image():
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return base64.b64encode(bytes(data)).decode("ascii")


@pytest.mark.parametrize("placeholder", [None, False, True])
@pytest.mark.parametrize("images", [False, True])
def test_pdf_placeholder_replaces_only_customer_line_and_fits_next_to_requisites(qt_app, tmp_path, placeholder, images):
    encoded = _test_image()
    details = {"executor": {"full_name": "Иванов Иван Иванович", "position": "Директор",
                            "signature_base64": encoded},
               "customer": {"full_name": "Петрова Мария Ивановна", "role": "Председатель совета МКД"},
               "insert_images": images, "stamp_base64": encoded}
    if placeholder is not None:
        details["customer_signature_placeholder"] = placeholder
    content = build_acceptance_act_pdf(organization_name="ООО УК «Тестовая»", house_address="Тестовый дом",
        act_number="1", act_date="31.01.2026", period_from="01.01.2026", period_to="31.01.2026",
        details=details, work_rows=[AcceptanceActWorkRow(name="Тестовая работа", actual_cost="100")])
    file = tmp_path / "act.pdf"
    file.write_bytes(content)
    document = QPdfDocument()
    document.load(str(file))
    assert document.pageCount() == 1
    text = document.getAllText(0).text()
    assert "Петрова Мария Ивановна" in text
    assert "Иванов Иван Иванович" in text
    assert "Исполнитель" in text and "Заказчик" in text
    assert (b"/Subtype /Image" in content) == images
    first_line = "Данные скрыты в целях безопасности"
    if placeholder:
        assert first_line in text and "персональных данных" in text
        box = document.getSelectionAtIndex(0, text.index(first_line), len(first_line)).boundingRectangle()
        customer = document.getSelectionAtIndex(0, text.index("Заказчик —"), len("Заказчик —")).boundingRectangle()
        assert box.left() > customer.right()
        assert 7 < box.height() < 12  # Readable ~8 pt text, not a 40-mm compressed stamp.
        assert box.right() < document.pagePointSize(0).width() - 18 * 72 / 25.4
    else:
        assert first_line not in text
    signature_text = text[text.index("Исполнитель —"):]
    assert signature_text.count("________________") == int(not images) + int(not placeholder)
    document.close()
