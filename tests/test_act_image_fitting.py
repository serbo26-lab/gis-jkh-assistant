"""Fictional ink only: physical PDF sizing, non-destructive crops and UI saves."""
import base64

import pytest
from PIL import Image, ImageChops, ImageDraw
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox
from shiboken6 import isValid

from act_test_data import populate_act_requisites
import test_ui_workflow as workflows
from test_standard_act_requisites import source as source_fixture
from gis_jkh_assistant.acceptance_act_details import resolve_act_details
from gis_jkh_assistant.acceptance_act_pdf import AcceptanceActWorkRow, build_acceptance_act_pdf
from gis_jkh_assistant.act_images import (
    decode_act_image, fitted_size, image_png, load_act_image, normalize_image_settings,
    prepare_act_image, signature_placement,
)
from gis_jkh_assistant.app.act_image_dialog import ActImageDialog
from gis_jkh_assistant.app.act_requisites_dialog import ActRequisitesDialog
from gis_jkh_assistant.app.styles import apply_theme
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore, initialize_act_schema
from gis_jkh_assistant.database.quality_assessment_store import QualityAssessmentStore
from gis_jkh_assistant.quality_automation import generate_acceptance_act_for_period
from gis_jkh_assistant.standard_acceptance_pdf import _SignatureField

qt_app = workflows.qt_app
desktop = workflows.desktop
source = source_fixture


def scan(scale=1):
    image = Image.new("RGB", (600 * scale, 800 * scale), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((100 * scale, 300 * scale, 300 * scale - 1, 500 * scale - 1),
                 outline="blue", width=3 * scale)
    draw.line((360 * scale, 100 * scale, 500 * scale, 140 * scale), fill="black", width=3 * scale)
    return image_png(image)


STAMP_CROP = [80 / 600, 280 / 800, 320 / 600, 520 / 800]


@pytest.mark.parametrize("scale", [1, 2])
def test_scan_margins_and_resolution_do_not_change_stamp_diameter(scale):
    content = scan(scale)
    prepared = prepare_act_image(content, {"crop": STAMP_CROP}, kind="stamp")
    assert prepared.size == (200 * scale, 200 * scale)
    assert prepared.getextrema()[3][0] == 0
    assert fitted_size(prepared.size, 40, 40) == (40, 40)
    assert decode_act_image(content).size == (600 * scale, 800 * scale)


def test_crop_rotation_white_background_and_existing_alpha():
    content = scan()
    settings = {"crop": [1 - STAMP_CROP[3], STAMP_CROP[0], 1 - STAMP_CROP[1], STAMP_CROP[2]], "rotation": 90}
    assert prepare_act_image(content, settings, kind="stamp").size == (200, 200)
    opaque = prepare_act_image(content, {"crop": STAMP_CROP, "remove_background": False}, kind="stamp")
    assert opaque.getextrema()[3] == (255, 255)
    transparent = Image.new("RGBA", (60, 30))
    ImageDraw.Draw(transparent).rectangle((10, 5, 49, 24), fill=(20, 50, 90, 160))
    prepared = prepare_act_image(image_png(transparent), kind="signature")
    assert prepared.size == (40, 20)
    assert prepared.getpixel((0, 0)) == (20, 50, 90, 160)


@pytest.mark.parametrize("size", [(1000, 50), (20, 600), (100, 100)])
@pytest.mark.parametrize("offset", [-5, 0, 5])
def test_signature_keeps_proportions_and_stays_inside_reserved_field(size, offset):
    x, y, width, height = signature_placement(size, {"offset_mm": offset})
    assert width / height == pytest.approx(size[0] / size[1])
    assert width <= 38 and height <= 13
    assert 0 <= x <= x + width <= 38
    assert 0 <= y <= y + height <= 26


@pytest.mark.parametrize("settings", [{"size_mm": float("nan")}, {"size_mm": 51}, {"crop": [0, 0, 0, 1]}, {"rotation": 5}])
def test_invalid_settings_rejected(settings):
    with pytest.raises(ValueError):
        normalize_image_settings(settings, kind="stamp")


def test_empty_crop_and_invalid_upload_explain_why(tmp_path):
    with pytest.raises(ValueError, match="нет видимой"):
        prepare_act_image(scan(), {"crop": [0, 0, .1, .1]}, kind="signature")
    file = tmp_path / "bad.png"
    file.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="PNG"):
        load_act_image(file)
    file.write_bytes(scan())
    assert load_act_image(file) == (scan(), "bad.png")


def test_signature_is_drawn_on_line_even_with_opaque_background():
    encoded = base64.b64encode(scan()).decode("ascii")
    field = _SignatureField(encoded, {"crop": STAMP_CROP, "remove_background": False})
    calls = []
    class Canvas:
        def drawImage(self, *args, **kwargs):
            calls.append("image")
        def setStrokeColor(self, *args):
            pass
        def setLineWidth(self, *args):
            pass
        def line(self, x1, y1, x2, y2):
            calls.append((x1, y1, x2, y2))
    field.canv = Canvas()
    field.draw()
    assert calls[0] == "image"
    assert calls[1] == pytest.approx((0, 8 * 72 / 25.4, 38 * 72 / 25.4, 8 * 72 / 25.4))


def test_settings_migrate_and_survive_person_edits_options_and_reopen(source):
    repo, wl = source
    organization_id = repo.get_house(wl.house_id).organization_id
    store = ActRequisitesStore(repo)
    person = store.list_people(organization_id=organization_id)[0]
    # Model a database created by the preceding version, retaining its blobs.
    repo.connection.execute("ALTER TABLE act_people DROP COLUMN signature_settings")
    repo.connection.execute("ALTER TABLE act_organization_options DROP COLUMN stamp_settings")
    initialize_act_schema(repo.connection)
    store.save_signature(person["id"], scan(), "original.png", {"offset_mm": 3})
    store.save_options(organization_id, stamp=scan(), stamp_name="seal.png", stamp_settings={"crop": STAMP_CROP, "size_mm": 40})
    store.save_person(dict(person, position="Руководитель"), person_id=person["id"], organization_id=organization_id)
    store.save_options(organization_id, insert_images=1)
    initialize_act_schema(repo.connection)
    fresh = ActRequisitesStore(repo)
    saved = fresh.list_people(organization_id=organization_id)[0]
    assert saved["signature"] == scan()
    assert saved["signature_settings"]["offset_mm"] == 3
    assert fresh.options(organization_id)["stamp_settings"]["crop"] == STAMP_CROP
    details = resolve_act_details(repo, wl.house_id, "31.01.2026")
    assert details["executor"]["signature_settings"]["offset_mm"] == 3
    assert details["stamp_settings"]["size_mm"] == 40
    snapshot = dict(details)
    store.save_options(organization_id, stamp=scan(), stamp_name="replacement.png")
    assert store.options(organization_id)["stamp_settings"]["crop"] == [0, 0, 1, 1]
    assert snapshot["stamp_settings"]["crop"] == STAMP_CROP


def test_saved_act_keeps_original_images_and_placement_after_live_edits(source):
    repo, wl = source
    organization_id = repo.get_house(wl.house_id).organization_id
    requisites = ActRequisitesStore(repo)
    director = requisites.list_people(organization_id=organization_id)[0]
    requisites.save_signature(director["id"], scan(), "original.png", {"crop": [.58, .1, .9, .2], "offset_mm": 2})
    requisites.save_options(organization_id, stamp=scan(), stamp_name="original.png",
                            stamp_settings={"crop": STAMP_CROP, "size_mm": 40}, insert_images=1)
    store = QualityAssessmentStore(repo)
    period = store.get_or_create_period(work_list_id=wl.id, year=2026, month=1)
    item = store.list_items(period.id)[0]
    store.update_item(item.id, included=True, cost=None, days_count=None, cost_reduction=None)
    fixation = repo.get_or_create_work_fixation_period(work_list_id=wl.id, year=2026, month=1)
    repo.update_work_fixation_item(repo.list_work_fixation_items(fixation.id)[0].id,
                                  fact_price="100", fact_amount="2", fact_count=1, fact_total_cost="200")
    generate_acceptance_act_for_period(store, period_id=period.id, organization_name="TEST", house_address="TEST")
    act = store.list_period_acts(period.id)[0]
    snapshot = requisites.snapshot(act.id)
    assert snapshot["stamp_settings"]["size_mm"] == 40
    assert snapshot["executor"]["signature_settings"]["offset_mm"] == 2
    assert base64.b64decode(snapshot["stamp_base64"]) == scan()
    requisites.save_signature(director["id"], scan(2), "new.png", {"offset_mm": -3})
    requisites.save_options(organization_id, stamp=scan(2), stamp_settings={"size_mm": 30})
    assert store.get_act(act.id).content == act.content
    assert requisites.snapshot(act.id) == snapshot


@pytest.mark.parametrize("rows,diameter", [(1, 40), (90, 50), (1, 10)])
def test_pdf_reserves_full_seal_size_and_keeps_signing_block_on_one_page(qt_app, tmp_path, rows, diameter):
    details = {"executor": {"full_name": "Тестовый Подписант", "position": "Директор"}, "customer": {},
               "insert_images": True, "stamp_base64": base64.b64encode(scan()).decode("ascii"),
               "stamp_settings": {"crop": STAMP_CROP, "size_mm": diameter},
               "customer_signature_placeholder": diameter == 10}
    details["executor"].update(signature_base64=base64.b64encode(scan()).decode("ascii"),
                               signature_settings={"crop": [.58, .1, .9, .2], "offset_mm": 5})
    pdf = build_acceptance_act_pdf(organization_name="Вымышленная УО", house_address="Тестовый дом",
        act_number="1", act_date="31.01.2026", period_from="01.01.2026", period_to="31.01.2026",
        work_rows=[AcceptanceActWorkRow(name=f"Работа {i}", actual_cost="100") for i in range(rows)], details=details)
    path = tmp_path / "sized.pdf"
    path.write_bytes(pdf)
    doc = QPdfDocument()
    try:
        assert doc.load(str(path)) == QPdfDocument.Error.None_
        last = doc.pageCount() - 1
        text = " ".join(doc.getAllText(last).text().split())
        assert "Подписи Сторон" in text and "Исполнитель" in text and "Заказчик" in text
        rendered = doc.render(last, QSize(1260, 1782))
        png = tmp_path / "rendered.png"
        assert rendered.save(str(png))
        image = Image.open(png).convert("RGB")
        red, green, blue = image.split()
        mask = ImageChops.subtract(blue, red).point(lambda x: 255 if x > 80 else 0)
        bounds = mask.getbbox()
        assert bounds is not None
        assert (bounds[2] - bounds[0]) * 210 / image.width == pytest.approx(diameter, abs=.7)
        assert (bounds[3] - bounds[1]) * 297 / image.height == pytest.approx(diameter, abs=.7)
    finally:
        doc.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_image_dialog_crop_rotate_preview_and_cancel(qt_app, theme):
    apply_theme(qt_app, theme)
    dialog = ActImageDialog("stamp", scan(), "fictional.png")
    try:
        dialog.show()
        qt_app.processEvents()
        bounds = dialog.region.image_rect()
        first = QPoint(round(bounds.left() + STAMP_CROP[0] * bounds.width()), round(bounds.top() + STAMP_CROP[1] * bounds.height()))
        last = QPoint(round(bounds.left() + STAMP_CROP[2] * bounds.width()), round(bounds.top() + STAMP_CROP[3] * bounds.height()))
        QTest.mousePress(dialog.region, Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseRelease(dialog.region, Qt.MouseButton.LeftButton, pos=last)
        assert dialog.prepared_size == (200, 200)
        dialog._rotate()
        assert dialog.prepared_size == (200, 200)
        dialog.fields["size_mm"].setValue(45)
        assert "45.0 × 45.0" in dialog.size_label.text()
        assert dialog.save_button.isEnabled()
        assert dialog.save_button.geometry().right() < dialog.width()
        dialog._crop_changed([0, 0, .1, .1])
        assert not dialog.save_button.isEnabled()
        assert "нет видимой" in dialog.error_label.text()
        dialog.reject()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.content == scan()
    finally:
        dialog.close()
        dialog.deleteLater()
        qt_app.processEvents()


@pytest.mark.parametrize("fail", [False, True])
def test_reedit_existing_image_cancel_and_failed_save_keep_previous_data(desktop, monkeypatch, fail):
    repo = desktop.repository
    populate_act_requisites(repo, desktop.work_list)
    store = ActRequisitesStore(repo)
    person = store.list_people(organization_id=desktop.organization.id)[0]
    second_id = store.save_person(dict(person, full_name="Второй Тестовый Подписант", valid_from="2021-01-01"),
                                  organization_id=desktop.organization.id)
    person = next(p for p in store.list_people(organization_id=desktop.organization.id) if p["id"] == second_id)
    store.save_signature(person["id"], scan(), "original.png", {"offset_mm": 2})
    owner = ActRequisitesDialog(repo, organization_id=desktop.organization.id)
    owner.table.selectRow(1)
    try:
        before = store.list_people(organization_id=desktop.organization.id)
        attempts = []
        def edit(dialog):
            attempts.append(dialog)
            assert dialog.content == scan()
            assert dialog.settings["offset_mm"] == (2 if len(attempts) == 1 else 4)
            dialog.settings["offset_mm"] = 4
            return QDialog.DialogCode.Accepted if len(attempts) == 1 else QDialog.DialogCode.Rejected
        monkeypatch.setattr(ActImageDialog, "exec", edit)
        errors = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
        if fail:
            repo.connection.execute("CREATE TRIGGER fail_image BEFORE UPDATE ON act_people BEGIN SELECT RAISE(ABORT, 'TEST'); END")
        owner._image("signature")
        if fail:
            assert len(attempts) == 2 and len(errors) == 1
            assert store.list_people(organization_id=desktop.organization.id) == before
            assert attempts[0] is attempts[1]  # failed save retains the edited dialog
        else:
            saved = store.list_people(organization_id=desktop.organization.id)[1]
            assert len(attempts) == 1 and not errors
            assert saved["signature"] == scan() and saved["signature_settings"]["offset_mm"] == 4
            assert owner._current()["id"] == person["id"]
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(attempts[0])
    finally:
        owner.close()
        owner.deleteLater()
