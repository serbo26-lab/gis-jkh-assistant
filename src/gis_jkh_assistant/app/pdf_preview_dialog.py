"""A local PDF preview; preparing a preview never attaches or uploads a document."""
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox, QVBoxLayout
from pathlib import Path


class PdfPreviewDialog(QDialog):
    def __init__(self, content, *, note="", parent=None):
        super().__init__(parent)
        self.content = content
        self.setWindowTitle("Предпросмотр акта — локальный документ")
        self.resize(860, 700)
        root = QVBoxLayout(self)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            root.addWidget(label)
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(content))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.document = QPdfDocument(self)
        self.document.load(self.buffer)
        view = QPdfView(self)
        view.setDocument(self.document)
        view.setPageMode(QPdfView.PageMode.MultiPage)
        view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        root.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить PDF как…")
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить копию PDF", "Акт.pdf", "PDF (*.pdf)")
        if path:
            try:
                Path(path if path.lower().endswith(".pdf") else path + ".pdf").write_bytes(self.content)
            except OSError as exc:
                QMessageBox.warning(self, "Не удалось сохранить PDF", str(exc))
