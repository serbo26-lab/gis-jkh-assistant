"""Assisted unpacked-extension setup; the browser remains in the user's control."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from shiboken6 import isValid

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from gis_jkh_assistant.browser.helper_installation import (
    detect_browsers, open_extensions_page, prepare_helper, prepare_helper_near_program,
    read_bundle, reveal_helper_folder, verify_installation,
)
from gis_jkh_assistant.browser.extensions_navigation import start_extensions_navigation
from gis_jkh_assistant.gis_integration.organization_context import (
    MIN_ORGANIZATION_ROUTING_HELPER_VERSION, helper_supports_organization_routing,
)
from gis_jkh_assistant.quality_assessment import (
    MIN_QUALITY_HELPER_VERSION, helper_supports_quality_assessment,
)

from .action_layout import ActionFlowLayout
from .error_messages import error_text
from .ui_scale import apply_layout_scale


REQUIRED_VERSION = ".".join(map(str, max(
    MIN_ORGANIZATION_ROUTING_HELPER_VERSION, MIN_QUALITY_HELPER_VERSION,
)))


def connection_message(status, bundle_version: str, *, waited: bool = False) -> str:
    """Only report what the local heartbeat actually proves."""
    if not status.running:
        return "Служба связи с браузером не запущена. " + (
            status.error or "Закройте мастер и перезапустите программу."
        )
    if not status.helper_connected:
        return (
            "Подключение пока не обнаружено. " if waited else "Связь с программой: ожидается помощник. "
        ) + "Проверьте, что браузер открыт и расширение включено."
    version = status.helper_version or "не определена"
    text = f"Связь с программой: подключён помощник, версия {version}."
    if not (
        helper_supports_organization_routing(status.helper_version)
        and helper_supports_quality_assessment(status.helper_version)
    ):
        text += f" Нужна версия {REQUIRED_VERSION} или новее. Загрузите подготовленную папку."
    elif status.helper_version != bundle_version:
        text += f" Версия подготовленных файлов: {bundle_version}."
    text += (
        " Вкладка ГИС найдена; вход и нужную УО проверьте в браузере."
        if status.gis_page_ready else " Вкладка ГИС не найдена. Откройте свой кабинет для дальнейшей работы."
    )
    return text


class HelperSetupDialog(QDialog):
    def __init__(self, gateway, parent=None, *, bundle=None, root=None, browsers=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Установка браузерного помощника")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.gateway = gateway
        self.bundle = bundle
        self.root = root
        self.selected_directory = None
        self.prepared_path = None
        self.browsers = list(detect_browsers() if browsers is None else browsers)
        self._waiting_since = time.monotonic()
        self._step = 0
        self._browser_navigation = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)
        self.step_title = self._label("")
        self.step_title.setObjectName("SectionTitle")
        outer.addWidget(self.step_title)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("PageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        # The viewport, rather than the longest instruction, controls width.
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.scroll.setWidget(self.stack)
        outer.addWidget(self.scroll, 1)
        self._selection_page()
        self._installation_page()
        self._connection_page()
        # Errors are part of the scrollable step, so recovery cannot hide Close.
        self.errors = []
        for index in range(3):
            label = self._label("")
            label.setObjectName("SaveStatus")
            label.setProperty("saveError", True)
            label.hide()
            self.stack.widget(index).layout().insertWidget(
                self.stack.widget(index).layout().count() - 1, label,
            )
            self.errors.append(label)
        footer = QHBoxLayout()
        self._footer = footer
        self.back = QPushButton("Назад")
        self.back.clicked.connect(lambda: self._show_step(self._step - 1))
        footer.addWidget(self.back)
        footer.addStretch(1)
        self.next = QPushButton()
        self.next.setObjectName("PrimaryButton")
        self.next.setDefault(True)
        self.next.clicked.connect(self._advance)
        footer.addWidget(self.next)
        self.close_button = QPushButton("Закрыть")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        outer.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self._refresh_connection)
        self.finished.connect(lambda _result: self.timer.stop())
        self.timer.start()
        self.navigation_timer = QTimer(self)
        self.navigation_timer.setInterval(120)
        self.navigation_timer.timeout.connect(self._poll_browser_navigation)
        self.finished.connect(self._cancel_browser_navigation)
        self._load_bundle()
        self._browser_changed()
        self._show_step(0)
        factor = getattr(parent, "_ui_scale", 1.0)
        apply_layout_scale(self, factor)
        screen = self.screen().availableGeometry()
        self.setMinimumHeight(min(360, screen.height() - 64))
        self._fit_footer()
        self.resize(
            max(self.minimumWidth(), min(round(740 * factor), screen.width() - 32)),
            min(round(510 * factor), screen.height() - 64),
        )

    @staticmethod
    def _label(text: str, *, muted: bool = False) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if muted:
            label.setObjectName("Muted")
        return label

    def _page(self):
        page = QWidget()
        page.setObjectName("PageContent")
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 4, 8, 4)
        body.setSpacing(10)
        self.stack.addWidget(page)
        return body

    @staticmethod
    def _actions(body, *buttons):
        actions = ActionFlowLayout()
        for button in buttons:
            button.setAutoDefault(False)
            actions.addWidget(button)
        body.addLayout(actions)

    def _selection_page(self):
        body = self._page()
        body.addWidget(self._label(
            "Программа подготовит файлы помощника. Вы добавите их в браузере и проверите связь. "
            "Скачивать и распаковывать расширение самостоятельно не нужно."
        ))
        browser_label = QLabel("&Браузер")
        self.browser_combo = QComboBox()
        self.browser_combo.setAccessibleName("Браузер для установки помощника")
        browser_label.setBuddy(self.browser_combo)
        for browser in self.browsers:
            self.browser_combo.addItem(self._browser_caption(browser))
        found = [index for index, browser in enumerate(self.browsers) if browser.executable]
        self.browser_combo.setCurrentIndex(found[0] if len(found) == 1 else -1)
        self.browser_combo.setPlaceholderText("Выберите браузер")
        self.browser_combo.currentIndexChanged.connect(self._browser_changed)
        body.addWidget(browser_label)
        body.addWidget(self.browser_combo)
        self.browser_advice = self._label("", muted=True)
        body.addWidget(self.browser_advice)
        self.browser_path = self._label("", muted=True)
        body.addWidget(self.browser_path)
        self.choose_browser = QPushButton("Указать файл браузера…")
        self.choose_browser.clicked.connect(self._choose_browser)
        self._actions(body, self.choose_browser)
        self.bundle_label = self._label("")
        body.addWidget(self.bundle_label)
        body.addWidget(self._label(
            "Используйте тот профиль браузера, в котором работаете с ГИС ЖКХ. "
            "Если браузер не найден, укажите его файл или установите расширение вручную.", muted=True,
        ))
        body.addStretch(1)

    @staticmethod
    def _browser_caption(browser):
        recommendation = {
            "chrome": " — рекомендуется",
            "edge": " — поддерживается",
            "yandex": " — не рекомендуется",
        }.get(browser.key, "")
        found = "найден" if browser.executable else "не найден"
        return f"{browser.name}{recommendation} · {found}"

    def _installation_page(self):
        body = self._page()
        self.prepared_label = self._label("")
        body.addWidget(self.prepared_label)
        body.addWidget(self._label(
            "1. Откройте настройки расширений выбранного браузера."
        ))
        self.open_browser = QPushButton("Открыть страницу расширений")
        self.open_browser.clicked.connect(self._open_browser)
        self.copy_url = QPushButton("Копировать адрес расширений")
        self.copy_url.clicked.connect(lambda: self._copy(
            self.extensions_url.text(),
            "Адрес скопирован. В браузере нажмите Ctrl+L (адресная строка), "
            "Ctrl+V (вставить адрес), Enter (открыть настройки).",
        ))
        self._actions(body, self.open_browser, self.copy_url)
        self.extensions_url = QLineEdit()
        self.extensions_url.setReadOnly(True)
        self.extensions_url.setAccessibleName("Адрес страницы расширений")
        body.addWidget(self.extensions_url)
        self.browser_steps = self._label("", muted=True)
        body.addWidget(self.browser_steps)
        body.addWidget(self._label(
            "2. Включите «Режим разработчика» и нажмите «Загрузить распакованное расширение»."
        ))
        body.addWidget(self._label(
            "3. В окне выбора папки вставьте путь кнопками Ctrl+V и Enter, затем подтвердите выбор папки."
        ))
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setAccessibleName("Папка с файлами помощника")
        self.path_edit.setToolTip("Выбирайте эту папку целиком: manifest.json находится внутри неё.")
        body.addWidget(self.path_edit)
        self.open_folder = QPushButton("Показать папку помощника")
        self.open_folder.clicked.connect(self._open_folder)
        self.copy_path = QPushButton("Копировать путь")
        self.copy_path.clicked.connect(self._copy_path)
        self._actions(body, self.open_folder, self.copy_path)
        body.addWidget(self._label(
            "Выберите именно папку помощника, а не файл manifest.json. "
            "Не перемещайте её после установки: браузер продолжит использовать эти файлы.", muted=True,
        ))
        self.yandex_notice = self._label(
            "Яндекс может отключить расширение после установки или перезапуска браузера. "
            "Откройте browser://extensions/ и включите его снова.", muted=True,
        )
        body.addWidget(self.yandex_notice)
        body.addWidget(self._label(
            "Если помощник уже установлен из другой папки, отключите прежнюю копию "
            "и подключите эту. Перезагрузка прежней копии не заменяет её файлы.", muted=True,
        ))
        self.feedback = self._label("", muted=True)
        body.addWidget(self.feedback)
        body.addStretch(1)

    def _connection_page(self):
        body = self._page()
        self.files_status = self._label("")
        body.addWidget(self.files_status)
        self.connection_status = self._label("")
        body.addWidget(self.connection_status)
        body.addWidget(self._label(
            "Подключение подтверждает связь с программой. Оно не определяет, в каком браузере "
            "и профиле установлен помощник, и не подтверждает вход в ГИС ЖКХ.", muted=True,
        ))
        body.addWidget(self._label(
            "Если связи нет: проверьте, что расширение включено, браузер открыт, "
            "а программа запущена. После добавления помощника обновите вкладку ГИС."
        ))
        body.addStretch(1)

    def _load_bundle(self):
        try:
            if self.bundle is None:
                self.bundle = read_bundle()
            self.bundle_label.setText(f"Версия файлов: {self.bundle.version} · Требуется программой: {REQUIRED_VERSION}")
        except (OSError, ValueError) as exc:
            self.bundle_label.setText("Файлы помощника недоступны.")
            self._show_error(exc, "Подготовка не выполнена.", "Проверьте комплект программы и повторите.")

    def _browser(self):
        index = self.browser_combo.currentIndex()
        return self.browsers[index] if 0 <= index < len(self.browsers) else None

    def _browser_changed(self):
        browser = self._browser()
        if not hasattr(self, "open_browser"):
            return
        self.choose_browser.setEnabled(browser is not None)
        self.browser_path.setText(
            str(browser.executable) if browser and browser.executable else
            (f"Файл {browser.executable_name} не найден." if browser else "Выберите браузер для инструкции.")
        )
        self.extensions_url.setText(browser.extensions_url if browser else "")
        self.open_browser.setEnabled(bool(browser and browser.executable))
        self.yandex_notice.setVisible(bool(browser and browser.key == "yandex"))
        advice = {
            "chrome": "Рекомендуется для помощника: при проверке Chrome сохранял "
                      "расширение включённым после перезапуска.",
            "edge": "Edge может спросить после перезапуска, оставить ли расширение "
                    "включённым. Подтвердите, если доверяете помощнику.",
            "yandex": "Не рекомендуется для помощника: Яндекс Браузер может "
                      "принудительно отключать распакованное расширение после перезапуска.",
        }
        self.browser_advice.setText(
            advice.get(browser.key, "") if browser
            else "Для установки помощника рекомендуем Google Chrome."
        )
        direct_open = bool(browser and browser.key == "chrome")
        self.open_browser.setText("Открыть страницу расширений")
        self.browser_steps.setText(
            ("Программа откроет расширения в отдельном окне Chrome. Если это не удалось:\n"
             if direct_open else
             "Программа откроет расширения в отдельном окне браузера. Если это не удалось:\n")
            + "1. Нажмите Ctrl+L — выделится адресная строка вверху браузера.\n"
            + "2. Нажмите Ctrl+V — вставится адрес настроек, скопированный программой.\n"
            + "3. Нажмите Enter — браузер откроет страницу управления расширениями."
        )
        if hasattr(self, "next") and self._step == 0:
            self.next.setEnabled(browser is not None)

    def _choose_browser(self):
        browser = self._browser()
        if browser is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, f"Файл {browser.name}", "", f"Браузер ({browser.executable_name});;Программы (*.exe)",
        )
        if not filename:
            return
        path = Path(filename).resolve()
        if not path.is_file() or path.name.casefold() != browser.executable_name.casefold():
            self._show_error(ValueError(f"Выберите файл {browser.executable_name}."), "Файл браузера не выбран.")
            return
        index = self.browser_combo.currentIndex()
        self.browsers[index] = replace(browser, executable=path)
        self.browser_combo.setItemText(index, self._browser_caption(self.browsers[index]))
        self._clear_error()
        self._browser_changed()

    def _prepare_files(self):
        if self.root is not None:
            # Explicit test/legacy destination keeps the isolated versioned layout.
            return prepare_helper(self.bundle, self.root)
        try:
            return prepare_helper_near_program(self.bundle, self.selected_directory)
        except OSError:
            if self.selected_directory is not None:
                raise
            chosen = QFileDialog.getExistingDirectory(
                self, "Выберите доступную папку для помощника",
                str(Path.home()),
            )
            if not chosen:
                raise PermissionError(
                    "Папка программы защищена от записи. Выберите доступное место "
                    "для папки browser-extension."
                ) from None
            target = Path(chosen)
            result = prepare_helper_near_program(self.bundle, target)
            self.selected_directory = target
            return result

    def _advance(self):
        if self._step == 0:
            if self._browser() is None:
                return
            self._clear_error()
            self.next.setEnabled(False)
            try:
                self._load_bundle()
                if self.bundle is None:
                    return
                self.prepared_path = self._prepare_files()
            except (OSError, ValueError) as exc:
                self._show_error(exc, "Подготовка не выполнена; прежние версии сохранены.")
                return
            finally:
                self.next.setEnabled(True)
            self.path_edit.setText(str(self.prepared_path))
            self.path_edit.setCursorPosition(0)
            self.prepared_label.setText(f"Файлы помощника {self.bundle.version} готовы. Выберите папку «{self.prepared_path.name}».")
            self.files_status.setText(f"Файлы подготовлены: версия {self.bundle.version}.")
            self._show_step(1)
        elif self._step == 1:
            if not self._revalidate_prepared():
                return
            self._waiting_since = time.monotonic()
            self._show_step(2)
        else:
            if self._revalidate_prepared():
                self._refresh_connection()

    def _show_step(self, step):
        self._cancel_browser_navigation()
        self._step = max(0, min(2, step))
        self.stack.setCurrentIndex(self._step)
        self.step_title.setText((
            "1 из 3 · Браузер и файлы", "2 из 3 · Добавление в браузер", "3 из 3 · Проверка связи",
        )[self._step])
        self.back.setVisible(self._step > 0)
        self.next.setText(("Подготовить файлы", "Проверить подключение", "Проверить ещё раз")[self._step])
        self.next.setEnabled(self._step != 0 or self._browser() is not None)
        self._fit_footer()
        self.scroll.verticalScrollBar().setValue(0)
        self._refresh_connection()
        self.next.setFocus()

    def reopen(self):
        """Recheck old ready claims without writing files on dialog open."""
        if self.prepared_path is not None:
            self._revalidate_prepared()
        apply_layout_scale(self, getattr(self.parent(), "_ui_scale", 1.0))
        self._fit_footer()
        self.timer.start()
        self._refresh_connection()

    def _revalidate_prepared(self):
        try:
            if self.prepared_path is None or self.bundle is None:
                raise ValueError("Файлы помощника ещё не подготовлены.")
            verify_installation(self.prepared_path, self.bundle)
        except (OSError, ValueError) as exc:
            previous_path = self.prepared_path
            self.prepared_path = None
            self.prepared_label.clear()
            self.files_status.clear()
            self.path_edit.clear()
            self._show_step(0)
            self._show_error(
                exc, f"Папка помощника недоступна или изменена: {previous_path}",
                "Если папка изменена, сохраните её отдельно. Затем нажмите «Подготовить файлы».",
            )
            return False
        return True

    def _fit_footer(self):
        """All step captions must fit even at the smallest allowed window size."""
        for button in (self.back, self.next, self.close_button):
            button.ensurePolished()
        metrics = self.next.fontMetrics()
        chrome = max(16, self.next.sizeHint().width() - metrics.horizontalAdvance(self.next.text()))
        primary_width = max(metrics.horizontalAdvance(text) for text in (
            "Подготовить файлы", "Проверить подключение", "Проверить ещё раз",
        )) + chrome
        self.next.setMinimumWidth(primary_width)
        self.back.setMinimumWidth(self.back.sizeHint().width())
        self.close_button.setMinimumWidth(self.close_button.sizeHint().width())
        margins = self.layout().contentsMargins()
        required = (
            self.back.minimumWidth() + primary_width + self.close_button.minimumWidth()
            + max(0, self._footer.spacing()) * 3 + margins.left() + margins.right()
        )
        self.setMinimumWidth(max(540, required))

    def changeEvent(self, event):  # noqa: N802 - Qt API
        super().changeEvent(event)
        if hasattr(self, "_footer") and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            QTimer.singleShot(0, self._fit_footer)

    def _show_error(self, exc, effect: str, recovery: str = ""):
        label = self.errors[self._step]
        label.setText(error_text(exc, effect=effect, recovery=recovery or "Проверьте доступ и повторите действие."))
        label.show()
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(label)
                          if isValid(self.scroll) and isValid(label) else None)

    def _clear_error(self):
        for label in self.errors:
            label.hide()
            label.clear()

    def _copy(self, value, message):
        QApplication.clipboard().setText(value)
        self.feedback.setText(message)

    def _copy_path(self):
        self._copy(self.path_edit.text(), "Путь скопирован. В окне выбора папки нажмите Ctrl+V и Enter, затем подтвердите выбор папки.")

    def _open_folder(self):
        self._clear_error()
        if self.prepared_path is None:
            return
        if reveal_helper_folder(self.prepared_path):
            self.feedback.setText("Папка помощника выделена в Проводнике. Выберите её целиком в браузере.")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.prepared_path.parent))):
            self.feedback.setText(
                f"Открыта папка с помощником. Найдите и выберите «{self.prepared_path.name}»."
            )
        else:
            self._show_error(
                OSError("Не удалось открыть папку."), "Файлы помощника сохранены.",
                "Скопируйте путь к папке и откройте его в проводнике.",
            )

    def _open_browser(self):
        self._clear_error()
        if self._browser_navigation is not None or not self._revalidate_prepared():
            return
        browser = self._browser()
        if browser is None:
            return
        address = self.extensions_url.text()
        QApplication.clipboard().setText(address)
        try:
            self._browser_navigation = start_extensions_navigation(browser)
        except Exception as exc:
            self.feedback.setText(f"Не удалось открыть браузер: {exc}. Откройте его вручную и вставьте адрес ниже.")
            return
        if self._browser_navigation is not None:
            self.open_browser.setEnabled(False)
            self.next.setEnabled(False)
            self.feedback.setText("Открываем расширения. Не переключайте окно, пока вводится адрес.")
            self.navigation_timer.start()
            return
        try:
            open_extensions_page(self._browser())
        except OSError as exc:
            self._show_error(
                exc, "Адрес настроек расширений скопирован.",
                "Откройте выбранный браузер самостоятельно и выполните три действия, "
                "описанные выше: Ctrl+L — адресная строка, Ctrl+V — вставка, Enter — переход.",
            )
        else:
            if self._browser().key == "chrome":
                self.feedback.setText(
                    "Chrome получил запрос открыть настройки расширений. Если открылось "
                    "обычное окно, нажмите Ctrl+L (адресная строка), Ctrl+V (вставить адрес), "
                    "Enter (переход) — адрес уже скопирован."
                )
            else:
                self.feedback.setText(
                    "Браузер запущен; адрес настроек скопирован. Чтобы открыть страницу "
                    "расширений, выполните три действия выше: Ctrl+L, Ctrl+V, Enter."
                )

    def _cancel_browser_navigation(self, *_args):
        if hasattr(self, "navigation_timer"):
            self.navigation_timer.stop()
        if self._browser_navigation is not None:
            self._browser_navigation.cancel()
            self._browser_navigation = None
        if hasattr(self, "open_browser"):
            browser = self._browser()
            self.open_browser.setEnabled(bool(browser and browser.executable))
        if hasattr(self, "next"):
            self.next.setEnabled(self._step != 0 or self._browser() is not None)

    def _poll_browser_navigation(self):
        if self._browser_navigation is None:
            return
        try:
            result = self._browser_navigation.poll()
        except Exception as exc:
            self._cancel_browser_navigation()
            self.feedback.setText(f"Автоматический переход остановлен: {exc}. Вставьте адрес ниже вручную.")
            return
        if result.state == "waiting":
            return
        self._cancel_browser_navigation()
        if result.state == "opened":
            QApplication.clipboard().setText(str(self.prepared_path))
            self.feedback.setText(
                "Страница расширений открыта. Включите «Режим разработчика» и нажмите "
                "«Загрузить распакованное расширение». Путь к папке уже скопирован: "
                "вставьте его в окне выбора папки кнопками Ctrl+V и Enter."
            )
        else:
            self.feedback.setText(result.message + " Нажмите «Копировать адрес расширений», затем Ctrl+L, Ctrl+V и Enter в браузере.")

    def _refresh_connection(self):
        if self._step != 2 or self.bundle is None:
            return
        try:
            text = connection_message(
                self.gateway.status(), self.bundle.version,
                waited=time.monotonic() - self._waiting_since >= 30,
            )
        except Exception as exc:
            text = error_text(exc, effect="Проверка связи не завершена.", recovery="Повторите проверку; файлы помощника сохранены.")
        self.connection_status.setText(text)
