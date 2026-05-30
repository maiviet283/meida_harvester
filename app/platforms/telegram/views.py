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
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.locales import translate
from app.platforms.common import UserFacingDownloadError
from app.platforms.telegram.service import CONFIG, TelegramService
from app.license_gate_ui import ensure_license_allowed
from app.update_ui import ensure_update_allowed


_STATE_INIT = "init"
_STATE_OTP_SENT = "otp_sent"
_STATE_2FA = "2fa"


class _AuthWorker(QObject):
    finished = pyqtSignal(bool, str, str)

    def __init__(
        self,
        service: TelegramService,
        action: str,
        **kwargs,
    ) -> None:
        super().__init__()
        self.service = service
        self.action = action
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            if self.action == "send_otp":
                partial, code_hash = self.service.send_otp(self.kwargs["phone"])
                self.finished.emit(True, partial, code_hash)
            elif self.action == "verify_otp":
                self.service.verify_otp(
                    self.kwargs["phone"],
                    self.kwargs["phone_code_hash"],
                    self.kwargs["code"],
                    self.kwargs["partial_session"],
                )
                self.finished.emit(True, "", "")
            elif self.action == "verify_2fa":
                self.service.verify_2fa(self.kwargs["password"])
                self.finished.emit(True, "", "")
        except UserFacingDownloadError as exc:
            self.finished.emit(False, exc.status_key, exc.data.get("error", ""))
        except Exception as exc:
            self.finished.emit(False, "download_failed", str(exc))


class _DownloadWorker(QObject):
    progress_changed = pyqtSignal(str, int, object)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        service: TelegramService,
        mode: str,
        url: str,
        folder: str,
    ) -> None:
        super().__init__()
        self.service = service
        self.mode = mode
        self.url = url
        self.folder = folder

    def run(self) -> None:
        try:
            if self.mode == "single":
                self.service.download_single(self.url, self.folder, self._emit)
            else:
                self.service.download_page(self.url, self.folder, self._emit)
            self.finished.emit(True, "")
        except UserFacingDownloadError as exc:
            pct = 0 if exc.status_key == "download_cancelled" else 100
            self.progress_changed.emit(exc.status_key, pct, exc.data)
            self.finished.emit(False, exc.status_key)
        except Exception as exc:
            self.progress_changed.emit("download_failed", 100, {"error": str(exc)})
            self.finished.emit(False, str(exc))

    def _emit(self, key: str, percent: int, data: object) -> None:
        self.progress_changed.emit(key, max(0, min(100, percent)), data or {})

    def request_cancel(self) -> None:
        self.service.request_cancel()


class _LoginPanel(QFrame):
    authenticated = pyqtSignal()

    def __init__(self, service: TelegramService, language_getter: Callable[[], str]) -> None:
        super().__init__()
        self.service = service
        self.language_getter = language_getter
        self._state = _STATE_INIT
        self._partial_session = ""
        self._phone_code_hash = ""
        self._thread: QThread | None = None
        self._worker: _AuthWorker | None = None
        self.setObjectName("downloadPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)

        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        layout.addWidget(self.title)

        self.desc = QLabel()
        self.desc.setObjectName("helperText")
        self.desc.setWordWrap(True)
        layout.addWidget(self.desc)

        self.phone_input = QLineEdit()
        self.phone_input.setMinimumHeight(42)
        self.phone_input.setPlaceholderText("+84xxxxxxxxx")
        layout.addWidget(self.phone_input)

        self.otp_input = QLineEdit()
        self.otp_input.setMinimumHeight(42)
        self.otp_input.setMaxLength(10)
        self.otp_input.setVisible(False)
        self.otp_input.returnPressed.connect(self._on_action)
        layout.addWidget(self.otp_input)

        self.password_input = QLineEdit()
        self.password_input.setMinimumHeight(42)
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setVisible(False)
        self.password_input.returnPressed.connect(self._on_action)
        layout.addWidget(self.password_input)

        self.status = QLabel()
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        layout.addStretch(1)

        self.action_btn = QPushButton()
        self.action_btn.setObjectName("primaryButton")
        self.action_btn.setMinimumHeight(44)
        self.action_btn.clicked.connect(self._on_action)
        layout.addWidget(self.action_btn)

        self.retranslate()

    def t(self, key: str, **kwargs) -> str:
        return translate(self.language_getter(), key, **kwargs)

    def retranslate(self) -> None:
        self.title.setText(self.t("telegram.login_title"))
        self.desc.setText(self.t("telegram.login_desc"))
        self.phone_input.setPlaceholderText(self.t("telegram.phone_placeholder"))
        self.otp_input.setPlaceholderText(self.t("telegram.otp_placeholder"))
        self.password_input.setPlaceholderText(self.t("telegram.password_placeholder"))
        if self._state == _STATE_INIT:
            self.action_btn.setText(self.t("telegram.send_otp"))
        elif self._state == _STATE_OTP_SENT:
            self.action_btn.setText(self.t("telegram.verify_otp"))
        elif self._state == _STATE_2FA:
            self.action_btn.setText(self.t("telegram.verify_2fa"))

    def _on_action(self) -> None:
        if self._state == _STATE_INIT:
            self._do_send_otp()
        elif self._state == _STATE_OTP_SENT:
            self._do_verify_otp()
        elif self._state == _STATE_2FA:
            self._do_verify_2fa()

    def _do_send_otp(self) -> None:
        phone = self.phone_input.text().strip()
        if not phone:
            self.status.setText(self.t("telegram.missing_credentials"))
            return
        self._set_busy(True)
        self.status.setText(self.t("telegram.sending_otp"))
        self._run_auth("send_otp", phone=phone)

    def _do_verify_otp(self) -> None:
        code = self.otp_input.text().strip()
        if not code:
            self.status.setText(self.t("telegram.missing_otp"))
            return
        self._set_busy(True)
        self.status.setText(self.t("telegram.verifying"))
        self._run_auth(
            "verify_otp",
            phone=self.phone_input.text().strip(),
            phone_code_hash=self._phone_code_hash,
            code=code,
            partial_session=self._partial_session,
        )

    def _do_verify_2fa(self) -> None:
        password = self.password_input.text()
        if not password:
            self.status.setText(self.t("telegram.missing_password"))
            return
        self._set_busy(True)
        self.status.setText(self.t("telegram.verifying"))
        self._run_auth("verify_2fa", password=password)

    def _run_auth(self, action: str, **kwargs) -> None:
        self._thread = QThread(self)
        self._worker = _AuthWorker(self.service, action, **kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_auth_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_auth_finished(self, success: bool, data1: str, data2: str) -> None:
        self._set_busy(False)
        if not success:
            self._on_auth_error(data1, data2)
            return

        if self._worker and self._worker.action == "send_otp":
            self._partial_session = data1
            self._phone_code_hash = data2
            self._state = _STATE_OTP_SENT
            self.otp_input.setVisible(True)
            self.otp_input.setFocus()
            self.action_btn.setText(self.t("telegram.verify_otp"))
            self.status.setText(self.t("telegram.otp_sent"))
        elif self._worker and self._worker.action == "verify_otp":
            self._state = _STATE_INIT
            self.authenticated.emit()
        elif self._worker and self._worker.action == "verify_2fa":
            self._state = _STATE_INIT
            self.authenticated.emit()

        self._thread = None
        self._worker = None

    def _on_auth_error(self, status_key: str, detail: str) -> None:
        self._thread = None
        self._worker = None

        if status_key == "telegram_2fa_required":
            self._state = _STATE_2FA
            self.otp_input.setVisible(False)
            self.password_input.setVisible(True)
            self.password_input.setFocus()
            self.action_btn.setText(self.t("telegram.verify_2fa"))
            self.status.setText(self.t("telegram.2fa_required"))
            return

        msg = self.t(f"status.{status_key}")
        if msg == f"status.{status_key}":
            msg = detail or status_key
        self.status.setText(msg)

    def _set_busy(self, busy: bool) -> None:
        self.action_btn.setEnabled(not busy)
        self.phone_input.setEnabled(not busy)
        self.otp_input.setEnabled(not busy)
        self.password_input.setEnabled(not busy)


class _DownloadPanel(QFrame):
    def __init__(
        self,
        service: TelegramService,
        mode: str,
        language_getter: Callable[[], str],
    ) -> None:
        super().__init__()
        self.service = service
        self.mode = mode
        self.language_getter = language_getter
        self.save_path = ""
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None
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

        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        self.path_input.setMinimumHeight(42)
        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("secondaryButton")
        self.browse_btn.setMinimumHeight(42)
        self.browse_btn.clicked.connect(self._choose_folder)
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(self.browse_btn)
        layout.addLayout(path_row)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.cancel_btn = QPushButton()
        self.cancel_btn.setObjectName("secondaryButton")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.setVisible(mode == "page")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        action_row.addWidget(self.cancel_btn)
        self.download_btn = QPushButton()
        self.download_btn.setObjectName("primaryButton")
        self.download_btn.setMinimumHeight(44)
        self.download_btn.clicked.connect(self._start)
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

    def t(self, key: str, **kwargs) -> str:
        return translate(self.language_getter(), key, **kwargs)

    def retranslate(self) -> None:
        is_single = self.mode == "single"
        self.header.setText(self.t("telegram.single_title" if is_single else "telegram.page_title"))
        self.helper.setText(self.t("telegram.single_hint" if is_single else "telegram.page_hint"))
        self.url_input.setPlaceholderText(
            CONFIG.example_video_url if is_single else CONFIG.example_page_url
        )
        self.path_input.setPlaceholderText(self.t("download.folder_placeholder"))
        self.browse_btn.setText(self.t("download.choose_folder"))
        self.download_btn.setText(self.t("download.download"))
        self.cancel_btn.setText(self.t("download.stop"))
        if self.progress.value() == 0:
            self.status.setText(self.t("download.ready"))
        if self.save_path:
            self.save_hint.setText(self.t("download.save_to", folder=self.save_path))

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self.t("download.choose_folder_dialog"))
        if folder:
            self.save_path = folder
            self.path_input.setText(folder)
            self.save_hint.setText(self.t("download.save_to", folder=folder))

    def _start(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self._warn(self.t("download.missing_url_title"), self.t("download.missing_url_message"))
            return
        if not self.save_path:
            self._choose_folder()
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
        self.cancel_btn.setEnabled(self.mode == "page")

        self._thread = QThread(self)
        self._worker = _DownloadWorker(self.service, self.mode, url, self.save_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _cancel(self) -> None:
        if self._worker:
            self._worker.request_cancel()
        self.cancel_btn.setEnabled(False)
        self.status.setText(self.t("status.cancelling"))

    def _on_progress(self, key: str, percent: int, data: object) -> None:
        values = data if isinstance(data, dict) else {}
        self.progress.setValue(percent)
        msg = self.t(f"status.{key}", **values)
        if msg == f"status.{key}":
            msg = self.t("status.downloading")
        self.status.setText(msg)

    def _on_finished(self, success: bool, _error: str) -> None:
        self.download_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if success:
            self.progress.setValue(100)
            self.status.setText(self.t("status.finished"))
        self._thread = None
        self._worker = None

    def _warn(self, title: str, message: str) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.addButton(self.t("dialog.ok"), QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()


class TelegramPage(QWidget):
    def __init__(
        self,
        language_getter: Callable[[], str],
        theme_getter: Callable[[], str],
    ) -> None:
        super().__init__()
        self.language_getter = language_getter
        self.theme_getter = theme_getter
        self.service = TelegramService()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 28)
        layout.setSpacing(16)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)
        self.title_label = QLabel()
        self.title_label.setObjectName("pageTitle")
        title_block.addWidget(self.title_label)
        self.desc_label = QLabel()
        self.desc_label.setObjectName("pageDescription")
        self.desc_label.setWordWrap(True)
        title_block.addWidget(self.desc_label)
        layout.addLayout(title_block)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self._login_panel = _LoginPanel(self.service, language_getter)
        self._login_panel.authenticated.connect(self._on_authenticated)
        self.stack.addWidget(self._login_panel)

        self._download_widget = QWidget()
        dl_layout = QVBoxLayout(self._download_widget)
        dl_layout.setContentsMargins(0, 0, 0, 0)
        dl_layout.setSpacing(10)

        user_bar = QFrame()
        user_bar.setObjectName("channelCard")
        user_bar_layout = QHBoxLayout(user_bar)
        user_bar_layout.setContentsMargins(12, 8, 12, 8)
        self.user_label = QLabel()
        self.user_label.setObjectName("channelName")
        user_bar_layout.addWidget(self.user_label, 1)
        self.logout_btn = QPushButton()
        self.logout_btn.setObjectName("secondaryButton")
        self.logout_btn.setFixedHeight(32)
        self.logout_btn.clicked.connect(self._logout)
        user_bar_layout.addWidget(self.logout_btn)
        dl_layout.addWidget(user_bar)

        self.tabs = QTabWidget()
        self._single_panel = _DownloadPanel(self.service, "single", language_getter)
        self._page_panel = _DownloadPanel(self.service, "page", language_getter)
        self.tabs.addTab(self._single_panel, "")
        self.tabs.addTab(self._page_panel, "")
        dl_layout.addWidget(self.tabs, 1)

        self.stack.addWidget(self._download_widget)

        if self.service.is_authenticated():
            self._show_download_view()

        self.retranslate()

    def t(self, key: str, **kwargs) -> str:
        return translate(self.language_getter(), key, **kwargs)

    def retranslate(self) -> None:
        self.title_label.setText(self.t("platforms.telegram.name"))
        self.desc_label.setText(self.t("platforms.telegram.description"))
        self.logout_btn.setText(self.t("telegram.logout"))
        self.tabs.setTabText(0, self.t("tabs.single"))
        self.tabs.setTabText(1, self.t("tabs.page"))
        self._login_panel.retranslate()
        self._single_panel.retranslate()
        self._page_panel.retranslate()

    def apply_theme(self) -> None:
        pass

    def _on_authenticated(self) -> None:
        self._show_download_view()

    def _show_download_view(self) -> None:
        me = self.service.get_me()
        if me:
            name = f"{me['first_name']} {me['last_name']}".strip()
            phone = me.get("phone", "")
            username = me.get("username", "")
            display = name
            if username:
                display += f"  @{username}"
            if phone:
                display += f"  +{phone}"
            self.user_label.setText(display)
        else:
            self.user_label.setText(self.t("telegram.logged_in"))
        self.stack.setCurrentIndex(1)

    def _logout(self) -> None:
        self.service.clear_session()
        self.stack.setCurrentIndex(0)


def create_page(
    language_getter: Callable[[], str],
    theme_getter: Callable[[], str],
) -> TelegramPage:
    return TelegramPage(language_getter, theme_getter)
