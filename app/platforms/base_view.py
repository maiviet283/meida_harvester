from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QComboBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yt_dlp.utils import DownloadCancelled

from app.cookie_store import get_cookie_store
from app.locales import translate
from app.platforms.common import BaseDownloadService, PlatformConfig, UserFacingDownloadError
from app.license_gate_ui import ensure_license_allowed
from app.update_ui import ensure_update_allowed


class DownloadWorker(QObject):
    progress_changed = pyqtSignal(str, int, object)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        service: BaseDownloadService,
        mode: str,
        url: str,
        folder: str,
        page_filter: str,
        cookie_header: str = "",
    ) -> None:
        super().__init__()
        self.service = service
        self.mode = mode
        self.url = url
        self.folder = folder
        self.page_filter = page_filter
        self.cookie_header = cookie_header

    def run(self) -> None:
        try:
            self.service.set_manual_cookie_header(self.cookie_header)
            if self.mode == "single":
                self.service.download_single(self.url, self.folder, self.emit_progress)
            else:
                self.service.download_page(self.url, self.folder, self.emit_progress, self.page_filter)
            self.finished.emit(True, "")
        except DownloadCancelled:
            self.progress_changed.emit("download_cancelled", 0, {})
            self.finished.emit(False, "download_cancelled")
        except UserFacingDownloadError as exc:
            percent = 0 if exc.status_key == "download_cancelled" else 100
            self.progress_changed.emit(exc.status_key, percent, exc.data)
            self.finished.emit(False, str(exc))
        except Exception as exc:
            self.progress_changed.emit("download_failed", 100, {"error": str(exc)})
            self.finished.emit(False, str(exc))

    def emit_progress(self, key: str, percent: int, data: dict[str, str] | None = None) -> None:
        self.progress_changed.emit(key, max(0, min(100, percent)), data or {})

    def request_cancel(self) -> None:
        self.service.request_cancel()


class DownloadPanel(QFrame):
    def __init__(
        self,
        config: PlatformConfig,
        service_cls: type[BaseDownloadService],
        mode: str,
        language_getter: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.service_cls = service_cls
        self.mode = mode
        self.language_getter = language_getter
        self.save_path = ""
        self.thread: QThread | None = None
        self.worker: DownloadWorker | None = None
        self.setObjectName("downloadPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)

        self.header = QLabel()
        self.header.setObjectName("panelTitle")
        layout.addWidget(self.header)

        self.helper = QLabel()
        self.helper.setObjectName("helperText")
        self.helper.setWordWrap(True)
        layout.addWidget(self.helper)

        self.url_input = QLineEdit()
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setMinimumHeight(42)
        layout.addWidget(self.url_input)

        self.cookie_hint = QLabel()
        self.cookie_hint.setObjectName("helperText")
        self.cookie_hint.setWordWrap(True)
        self.cookie_hint.setVisible(self.config.supports_manual_cookies)
        layout.addWidget(self.cookie_hint)

        self.cookie_input = QLineEdit()
        self.cookie_input.setClearButtonEnabled(True)
        self.cookie_input.setMinimumHeight(42)
        self.cookie_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie_input.setVisible(self.config.supports_manual_cookies)
        layout.addWidget(self.cookie_input)

        self.page_filter_box = QComboBox()
        self.page_filter_box.setObjectName("filterCombo")
        self.page_filter_box.setMinimumHeight(42)
        self.page_filter_box.addItem("", "short")
        self.page_filter_box.addItem("", "long")
        self.page_filter_box.addItem("", "all")
        self.page_filter_box.setVisible(self.mode == "page" and self.config.supports_page_filters)
        layout.addWidget(self.page_filter_box)

        path_row = QHBoxLayout()
        path_row.setSpacing(10)

        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        self.path_input.setMinimumHeight(42)

        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("secondaryButton")
        self.browse_btn.setMinimumHeight(42)
        self.browse_btn.clicked.connect(self.choose_folder)

        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(self.browse_btn)
        layout.addLayout(path_row)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.cancel_btn = QPushButton()
        self.cancel_btn.setObjectName("secondaryButton")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.setVisible(self.mode == "page")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_download)
        action_row.addWidget(self.cancel_btn)

        self.download_btn = QPushButton()
        self.download_btn.setObjectName("primaryButton")
        self.download_btn.setMinimumHeight(44)
        self.download_btn.clicked.connect(self.start_download)
        action_row.addWidget(self.download_btn)
        layout.addLayout(action_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.status = QLabel()
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.save_hint = QLabel()
        self.save_hint.setObjectName("savePathText")
        self.save_hint.setWordWrap(True)
        layout.addWidget(self.save_hint)

        layout.addStretch(1)
        self.retranslate()

    def t(self, key: str, **kwargs: str) -> str:
        return translate(self.language_getter(), key, **kwargs)

    def platform_name(self) -> str:
        return self.t(f"platforms.{self.config.key}.name")

    def retranslate(self) -> None:
        is_single = self.mode == "single"
        self.header.setText(self.t("download.single_title" if is_single else "download.page_title"))
        self.helper.setText(
            self.t(
                "download.single_hint" if is_single else "download.page_hint",
                platform=self.platform_name(),
            )
        )
        self.url_input.setPlaceholderText(
            self.config.example_video_url if is_single else self.config.example_page_url
        )
        self.cookie_hint.setText(self.t("download.cookie_hint"))
        self.cookie_input.setPlaceholderText(self.t("download.cookie_placeholder"))
        self.path_input.setPlaceholderText(self.t("download.folder_placeholder"))
        self.browse_btn.setText(self.t("download.choose_folder"))
        self.download_btn.setText(self.t("download.download"))
        self.cancel_btn.setText(self.t("download.stop"))
        self.page_filter_box.setItemText(0, self.t("download.page_filter_short"))
        self.page_filter_box.setItemText(1, self.t("download.page_filter_long"))
        self.page_filter_box.setItemText(2, self.t("download.page_filter_all"))
        if self.progress.value() == 0:
            self.status.setText(self.t("download.ready"))
        if self.save_path:
            self.save_hint.setText(self.t("download.save_to", folder=self.save_path))

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.t("download.choose_folder_dialog"))
        if folder:
            self.save_path = folder
            self.path_input.setText(folder)
            self.save_hint.setText(self.t("download.save_to", folder=folder))

    def show_warning(self, title: str, message: str) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.addButton(self.t("dialog.ok"), QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

    def start_download(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.show_warning(self.t("download.missing_url_title"), self.t("download.missing_url_message"))
            return
        if not self.save_path:
            self.choose_folder()
            if not self.save_path:
                return
        if not ensure_license_allowed():
            return
        if not ensure_update_allowed(self.language_getter()):
            return

        self.progress.setValue(0)
        self.status.setText(self.t("status.preparing"))
        self.download_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.cookie_input.setEnabled(False)
        self.page_filter_box.setEnabled(False)
        self.cancel_btn.setEnabled(self.mode == "page")

        self.thread = QThread(self)
        page_filter = self.page_filter_box.currentData() if self.page_filter_box.isVisible() else "all"
        cookie_header = self.cookie_input.text().strip() if self.cookie_input.isVisible() else ""
        self.worker = DownloadWorker(
            self.service_cls(),
            self.mode,
            url,
            self.save_path,
            str(page_filter),
            cookie_header,
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.finished.connect(self.finish_download)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def cancel_download(self) -> None:
        if not self.worker:
            return
        self.worker.request_cancel()
        self.cancel_btn.setEnabled(False)
        self.status.setText(self.t("status.cancelling"))

    def update_progress(self, key: str, percent: int, data: object) -> None:
        values = data if isinstance(data, dict) else {}
        self.progress.setValue(percent)
        self.status.setText(self.t(f"status.{key}", **values))

    def finish_download(self, success: bool, error: str) -> None:
        self.download_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.cookie_input.setEnabled(True)
        self.page_filter_box.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if success:
            self.progress.setValue(100)
            if self.status.text() != self.t("status.finished"):
                self.status.setText(self.t("status.finished"))
        self.thread = None
        self.worker = None


class PlatformPage(QWidget):
    def __init__(
        self,
        config: PlatformConfig,
        service_cls: type[BaseDownloadService],
        language_getter: Callable[[], str],
        theme_getter: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.service_cls = service_cls
        self.language_getter = language_getter
        self.theme_getter = theme_getter
        self.cookie_store = get_cookie_store()
        self.syncing_cookie = False
        self.cookie_help_btn: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(16)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)

        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        title_block.addWidget(self.title)

        self.description = QLabel()
        self.description.setObjectName("pageDescription")
        self.description.setWordWrap(True)
        title_block.addWidget(self.description)
        layout.addLayout(title_block)

        self.tabs = QTabWidget()
        if self.config.supports_manual_cookies:
            self.cookie_help_btn = QPushButton()
            self.cookie_help_btn.setObjectName("tabHelpButton")
            self.cookie_help_btn.clicked.connect(self.show_cookie_help)
            self.tabs.setCornerWidget(self.cookie_help_btn, Qt.Corner.TopRightCorner)

        self.single_panel = DownloadPanel(config, service_cls, "single", language_getter)
        self.page_panel = DownloadPanel(config, service_cls, "page", language_getter)
        self.tabs.addTab(self.single_panel, "")
        self.tabs.addTab(self.page_panel, "")
        layout.addWidget(self.tabs, 1)
        self.setup_cookie_sync()
        self.retranslate()
        self.apply_theme()

    def t(self, key: str, **kwargs: str) -> str:
        return translate(self.language_getter(), key, **kwargs)

    def retranslate(self) -> None:
        self.title.setText(self.t(f"platforms.{self.config.key}.name"))
        self.description.setText(self.t(f"platforms.{self.config.key}.description"))
        self.tabs.setTabText(0, self.t("tabs.single"))
        self.tabs.setTabText(1, self.t("tabs.page"))
        if self.cookie_help_btn:
            self.cookie_help_btn.setText(self.t("download.cookie_help_button"))
        self.single_panel.retranslate()
        self.page_panel.retranslate()

    def apply_theme(self) -> None:
        return

    def show_cookie_help(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.t("download.cookie_help_title"))
        dialog.setText(self.t("download.cookie_help_message"))
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.addButton(self.t("dialog.ok"), QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

    def setup_cookie_sync(self) -> None:
        if not self.config.supports_manual_cookies:
            return

        saved_cookie = self.cookie_store.get(self.config.key)
        self.set_panel_cookie(self.single_panel, saved_cookie)
        self.set_panel_cookie(self.page_panel, saved_cookie)
        self.single_panel.cookie_input.textChanged.connect(self.sync_cookie_text)
        self.page_panel.cookie_input.textChanged.connect(self.sync_cookie_text)

    def sync_cookie_text(self, cookie_header: str) -> None:
        if self.syncing_cookie:
            return
        self.syncing_cookie = True
        try:
            for panel in (self.single_panel, self.page_panel):
                if panel.cookie_input.text() != cookie_header:
                    self.set_panel_cookie(panel, cookie_header)
            self.cookie_store.set(self.config.key, cookie_header)
        finally:
            self.syncing_cookie = False

    def set_panel_cookie(self, panel: DownloadPanel, cookie_header: str) -> None:
        was_blocked = panel.cookie_input.blockSignals(True)
        panel.cookie_input.setText(cookie_header)
        panel.cookie_input.blockSignals(was_blocked)
