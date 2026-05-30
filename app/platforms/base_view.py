from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QComboBox,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
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
        self.cleaning_url_input = False
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
        self.url_input.textChanged.connect(self.clean_url_input)
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

    def clean_url_input(self, text: str) -> None:
        if self.cleaning_url_input:
            return
        cleaned_url = self.service_cls().clean_input_url(text, self.mode)
        if cleaned_url == text:
            return
        self.cleaning_url_input = True
        try:
            self.url_input.setText(cleaned_url)
        finally:
            self.cleaning_url_input = False

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
        service = self.service_cls()
        cleaned_url = service.clean_input_url(url, self.mode)
        if cleaned_url != url:
            url = cleaned_url
            self.url_input.setText(url)
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
            service,
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


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_date(upload_date: str) -> str:
    if len(upload_date) == 8:
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return upload_date


class _NumericItem(QTableWidgetItem):
    def __init__(self, value: int | float, display: str) -> None:
        super().__init__(display)
        self._numeric = value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumericItem):
            return self._numeric < other._numeric
        return super().__lt__(other)


_ANALYZE_COLS = ["rank", "title", "views", "likes", "comments", "shares", "engage", "duration", "hashtags", "date", "url"]
_COL_RANK, _COL_TITLE, _COL_VIEWS, _COL_LIKES, _COL_COMMENTS, _COL_SHARES, _COL_ENGAGE, _COL_DURATION, _COL_HASHTAGS, _COL_DATE, _COL_URL = range(11)


def _suggest_filename(url: str, platform_key: str) -> str:
    match = re.search(r"@([\w.]+)", url)
    identifier = match.group(1) if match else "profile"
    return f"analyze_{platform_key}_{identifier}.csv"


def _suggest_chart_filename(url: str, platform_key: str) -> str:
    match = re.search(r"@([\w.]+)", url)
    identifier = match.group(1) if match else "profile"
    return f"chart_{platform_key}_{identifier}.png"


def _compute_top_hashtags(videos: list[dict], n: int = 5) -> list[tuple[str, int, int]]:
    counts: Counter[str] = Counter()
    views_by_tag: dict[str, int] = {}
    for video in videos:
        tags_str = video.get("hashtags", "")
        v = video.get("view_count", 0)
        for raw in tags_str.split():
            tag = raw.lstrip("#")
            if tag:
                counts[tag] += 1
                views_by_tag[tag] = views_by_tag.get(tag, 0) + v
    sorted_tags = sorted(counts, key=lambda t: (-counts[t], -views_by_tag.get(t, 0)))
    return [(t, counts[t], views_by_tag.get(t, 0)) for t in sorted_tags[:n]]


def _compute_date_stats(videos: list[dict]) -> dict[str, str]:
    dates: list[datetime] = []
    for v in videos:
        d = v.get("upload_date", "")
        if len(d) == 8:
            try:
                dates.append(datetime.strptime(d, "%Y%m%d"))
            except ValueError:
                pass
    if len(dates) < 2:
        return {}
    dates.sort()
    span = (dates[-1] - dates[0]).days
    avg = round(span / (len(dates) - 1), 1)
    return {
        "newest": dates[-1].strftime("%Y-%m-%d"),
        "oldest": dates[0].strftime("%Y-%m-%d"),
        "avg_days": str(avg),
    }


class AnalysisWorker(QObject):
    progress_changed = pyqtSignal(str, int, object)
    video_found = pyqtSignal(object)
    channel_found = pyqtSignal(object)
    finished = pyqtSignal(bool, object, str)

    def __init__(self, service: BaseDownloadService, url: str) -> None:
        super().__init__()
        self.service = service
        self.url = url

    def run(self) -> None:
        try:
            results = self.service.analyze_page(
                self.url,
                self.emit_progress,
                on_video=self.on_video_found,
                on_channel=self.on_channel_found,
            )
            self.finished.emit(True, results, "")
        except DownloadCancelled:
            self.progress_changed.emit("analyze_cancelled", 0, {})
            self.finished.emit(False, [], "analyze_cancelled")
        except Exception as exc:
            self.progress_changed.emit("analyze_failed", 100, {"error": str(exc)})
            self.finished.emit(False, [], str(exc))

    def emit_progress(self, key: str, percent: int, data: dict | None = None) -> None:
        self.progress_changed.emit(key, max(0, min(100, percent)), data or {})

    def on_video_found(self, video: dict) -> None:
        self.video_found.emit(video)

    def on_channel_found(self, info: dict) -> None:
        self.channel_found.emit(info)

    def request_cancel(self) -> None:
        self.service.request_cancel()


class AnalysisPanel(QFrame):
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
        self.thread: QThread | None = None
        self.worker: AnalysisWorker | None = None
        self._results: list[dict] = []
        self._channel_info: dict = {}
        self.setObjectName("downloadPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)

        self.header = QLabel()
        self.header.setObjectName("panelTitle")
        layout.addWidget(self.header)

        self.helper = QLabel()
        self.helper.setObjectName("helperText")
        self.helper.setWordWrap(True)
        layout.addWidget(self.helper)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.url_input = QLineEdit()
        self.url_input.setClearButtonEnabled(True)
        self.url_input.setMinimumHeight(42)
        input_row.addWidget(self.url_input, 1)

        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("secondaryButton")
        self.stop_btn.setFixedHeight(42)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.cancel_analysis)
        input_row.addWidget(self.stop_btn)

        self.analyze_btn = QPushButton()
        self.analyze_btn.setObjectName("primaryButton")
        self.analyze_btn.setFixedHeight(42)
        self.analyze_btn.clicked.connect(self.start_analysis)
        input_row.addWidget(self.analyze_btn)
        layout.addLayout(input_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.status = QLabel()
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._filler = QWidget()
        self._filler.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._filler)

        self._channel_card = QFrame()
        self._channel_card.setObjectName("channelCard")
        card_layout = QVBoxLayout(self._channel_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(3)
        self._ch_name_label = QLabel()
        self._ch_name_label.setObjectName("channelName")
        self._ch_name_label.setTextFormat(Qt.TextFormat.RichText)
        card_layout.addWidget(self._ch_name_label)
        self._ch_bio_label = QLabel()
        self._ch_bio_label.setObjectName("channelBio")
        self._ch_bio_label.setWordWrap(True)
        card_layout.addWidget(self._ch_bio_label)
        self._channel_card.setVisible(False)
        layout.addWidget(self._channel_card)

        self.stats = QLabel()
        self.stats.setObjectName("helperText")
        self.stats.setWordWrap(True)
        self.stats.setVisible(False)
        layout.addWidget(self.stats)

        self._dates_label = QLabel()
        self._dates_label.setObjectName("overviewInfo")
        self._dates_label.setWordWrap(True)
        self._dates_label.setVisible(False)
        layout.addWidget(self._dates_label)

        self._hashtags_label = QLabel()
        self._hashtags_label.setObjectName("hashtagInfo")
        self._hashtags_label.setWordWrap(True)
        self._hashtags_label.setVisible(False)
        layout.addWidget(self._hashtags_label)

        summary_row = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("helperText")
        self.summary.setVisible(False)
        summary_row.addWidget(self.summary, 1)

        self._chart_btn = QPushButton()
        self._chart_btn.setObjectName("secondaryButton")
        self._chart_btn.setFixedHeight(34)
        self._chart_btn.setVisible(False)
        self._chart_btn.clicked.connect(self._open_chart)
        summary_row.addWidget(self._chart_btn)
        layout.addLayout(summary_row)

        self.table = QTableWidget()
        self.table.setObjectName("analysisTable")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setVisible(False)
        self.table.setMinimumHeight(200)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnCount(len(_ANALYZE_COLS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setWordWrap(False)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.table)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self.copy_url_btn = QPushButton()
        self.copy_url_btn.setObjectName("secondaryButton")
        self.copy_url_btn.setFixedHeight(36)
        self.copy_url_btn.setEnabled(False)
        self.copy_url_btn.clicked.connect(self.copy_selected_url)
        bottom_row.addWidget(self.copy_url_btn)

        self.reset_btn = QPushButton()
        self.reset_btn.setObjectName("secondaryButton")
        self.reset_btn.setFixedHeight(36)
        self.reset_btn.clicked.connect(self.reset_analysis)
        bottom_row.addWidget(self.reset_btn)

        bottom_row.addStretch(1)

        self.export_btn = QPushButton()
        self.export_btn.setObjectName("secondaryButton")
        self.export_btn.setFixedHeight(36)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_csv)
        bottom_row.addWidget(self.export_btn)
        layout.addLayout(bottom_row)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.retranslate()

    def t(self, key: str, **kwargs: str) -> str:
        return translate(self.language_getter(), key, **kwargs)

    def platform_name(self) -> str:
        return self.t(f"platforms.{self.config.key}.name")

    def retranslate(self) -> None:
        self.header.setText(self.t("analyze.title"))
        self.helper.setText(self.t("analyze.hint", platform=self.platform_name()))
        self.url_input.setPlaceholderText(self.config.example_page_url)
        self.analyze_btn.setText(self.t("analyze.start"))
        self.stop_btn.setText(self.t("analyze.stop"))
        self.reset_btn.setText(self.t("analyze.reset"))
        self.export_btn.setText(self.t("analyze.export_csv"))
        self.copy_url_btn.setText(self.t("analyze.copy_url"))
        self._chart_btn.setText(self.t("analyze.view_chart"))
        self.table.setHorizontalHeaderLabels([self.t(f"analyze.col_{c}") for c in _ANALYZE_COLS])
        if self.progress.value() == 0 and not self._results:
            self.status.setText(self.t("download.ready"))
        if self._channel_info:
            self._update_channel_card(self._channel_info)
        if self._results:
            self._rebuild_overview()

    def show_warning(self, title: str, message: str) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.addButton(self.t("dialog.ok"), QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

    def _set_results_visible(self, visible: bool) -> None:
        self._filler.setVisible(not visible)
        self.table.setVisible(visible)
        has = visible and bool(self._results)
        self.stats.setVisible(has)
        self._dates_label.setVisible(has)
        self._hashtags_label.setVisible(has)
        self.summary.setVisible(has)
        self._chart_btn.setVisible(has)

    def start_analysis(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.show_warning(self.t("analyze.missing_url_title"), self.t("analyze.missing_url_message"))
            return
        if not ensure_license_allowed():
            return
        if not ensure_update_allowed(self.language_getter()):
            return

        self.progress.setValue(0)
        self.status.setText(self.t("status.preparing"))
        self.analyze_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.copy_url_btn.setEnabled(False)
        self.table.setRowCount(0)
        self.table.setSortingEnabled(False)
        self._results = []
        self._channel_info = {}
        self._channel_card.setVisible(False)
        self._set_results_visible(False)

        service = self.service_cls()
        self.thread = QThread(self)
        self.worker = AnalysisWorker(service, url)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress_changed.connect(self.update_progress)
        self.worker.video_found.connect(self.on_video_found)
        self.worker.channel_found.connect(self.on_channel_found)
        self.worker.finished.connect(self.finish_analysis)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def cancel_analysis(self) -> None:
        if self.worker:
            self.worker.request_cancel()
        self.stop_btn.setEnabled(False)
        self.status.setText(self.t("status.cancelling"))

    def reset_analysis(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        self.url_input.clear()
        self.progress.setValue(0)
        self.status.setText(self.t("download.ready"))
        self.table.setRowCount(0)
        self.table.setSortingEnabled(False)
        self._results = []
        self._channel_info = {}
        self._channel_card.setVisible(False)
        self.export_btn.setEnabled(False)
        self.copy_url_btn.setEnabled(False)
        self._set_results_visible(False)

    def update_progress(self, key: str, percent: int, data: object) -> None:
        values = data if isinstance(data, dict) else {}
        self.progress.setValue(percent)
        self.status.setText(self.t(f"status.{key}", **values))

    def on_video_found(self, video: object) -> None:
        if not isinstance(video, dict):
            return
        self._results.append(video)
        self._append_row(video)
        if not self.table.isVisible():
            self._filler.setVisible(False)
            self.table.setVisible(True)

    def on_channel_found(self, info: object) -> None:
        if not isinstance(info, dict):
            return
        self._channel_info = info
        self._update_channel_card(info)
        self._channel_card.setVisible(True)

    def _update_channel_card(self, info: dict) -> None:
        name = info.get("name", "")
        username = info.get("username", "")
        bio = info.get("bio", "") or self.t("analyze.channel_no_bio")
        bio_display = bio[:160] + "…" if len(bio) > 160 else bio

        name_html = f"<b>{name}</b>"
        if username:
            name_html += f"&nbsp;&nbsp;<span style='font-weight:400'>@{username}</span>"
        self._ch_name_label.setText(name_html)

        bio_label = self.t("analyze.channel_bio_label")
        self._ch_bio_label.setText(f"<b>{bio_label}:</b> {bio_display}")
        self._ch_bio_label.setToolTip(bio)

    def _rebuild_overview(self) -> None:
        if not self._results:
            return
        videos = self._results

        total_views = sum(v.get("view_count", 0) for v in videos)
        avg_views = total_views // len(videos) if videos else 0
        max_views = videos[0].get("view_count", 0) if videos else 0
        self.stats.setText(self.t("analyze.stats",
            total_views=_fmt_count(total_views),
            avg_views=_fmt_count(avg_views),
            max_views=_fmt_count(max_views),
        ))

        date_stats = _compute_date_stats(videos)
        if date_stats:
            self._dates_label.setText(self.t("analyze.dates_info",
                newest=date_stats["newest"],
                oldest=date_stats["oldest"],
                avg_days=date_stats["avg_days"],
            ))
        else:
            self._dates_label.setText(self.t("analyze.dates_no_data"))

        top_tags = _compute_top_hashtags(videos)
        if top_tags:
            parts = [self.t("analyze.hashtag_item", tag=tag, count=str(count))
                     for tag, count, _ in top_tags]
            label = self.t("analyze.top_hashtags_label")
            self._hashtags_label.setText(f"{label}: {' · '.join(parts)}")
        else:
            self._hashtags_label.setText("")

        self.summary.setText(self.t("analyze.summary", count=str(len(videos))))
        self._chart_btn.setText(self.t("analyze.view_chart"))

    def _open_chart(self) -> None:
        if not self._results:
            return
        try:
            from matplotlib.figure import Figure  # noqa: F401
        except ImportError:
            dialog = QMessageBox(self)
            dialog.setWindowTitle(self.t("analyze.chart_title"))
            dialog.setText("matplotlib not found. Run: pip install matplotlib")
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.addButton(self.t("dialog.ok"), QMessageBox.ButtonRole.AcceptRole)
            dialog.exec()
            return
        dlg = ChartDialog(
            self._results,
            self._channel_info,
            self.url_input.text().strip(),
            self.config.key,
            self.theme_getter(),
            self.language_getter,
            parent=self,
        )
        dlg.exec()

    def finish_analysis(self, success: bool, results: object, error: str) -> None:
        self.analyze_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.thread = None
        self.worker = None

        if not self._results:
            return

        self._results = sorted(self._results, key=lambda v: v.get("view_count") or 0, reverse=True)
        self._populate_table(self._results)
        self.export_btn.setEnabled(True)

        self._rebuild_overview()
        self._filler.setVisible(False)
        self.table.setVisible(True)
        self.stats.setVisible(True)
        self._dates_label.setVisible(True)
        self._hashtags_label.setVisible(True)
        self.summary.setVisible(True)
        self._chart_btn.setVisible(True)

    def _append_row(self, video: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._fill_row(row, video, rank=row + 1)

    def _populate_table(self, videos: list[dict]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(videos))
        for rank, video in enumerate(videos, 1):
            self._fill_row(rank - 1, video, rank=rank)
        self._resize_columns()
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(_COL_VIEWS, Qt.SortOrder.DescendingOrder)

    def _fill_row(self, row: int, video: dict, rank: int) -> None:
        def _center(item: QTableWidgetItem) -> QTableWidgetItem:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return item

        def _right(item: QTableWidgetItem) -> QTableWidgetItem:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return item

        self.table.setItem(row, _COL_RANK, _center(_NumericItem(rank, str(rank))))

        title_item = QTableWidgetItem(video.get("title", ""))
        title_item.setToolTip(video.get("title", ""))
        self.table.setItem(row, _COL_TITLE, title_item)

        for col, field in (
            (_COL_VIEWS, "view_count"),
            (_COL_LIKES, "like_count"),
            (_COL_COMMENTS, "comment_count"),
            (_COL_SHARES, "repost_count"),
        ):
            val = int(video.get(field) or 0)
            self.table.setItem(row, col, _right(_NumericItem(val, _fmt_count(val))))

        engage = float(video.get("engage_rate") or 0)
        self.table.setItem(row, _COL_ENGAGE, _right(_NumericItem(engage, f"{engage:.1f}%")))

        dur = int(video.get("duration") or 0)
        self.table.setItem(row, _COL_DURATION, _center(_NumericItem(dur, _fmt_duration(dur))))

        self.table.setItem(row, _COL_HASHTAGS, QTableWidgetItem(video.get("hashtags", "")))

        self.table.setItem(row, _COL_DATE, _center(QTableWidgetItem(_fmt_date(video.get("upload_date", "")))))

        url = video.get("url", "")
        url_item = QTableWidgetItem(url)
        url_item.setToolTip(url)
        self.table.setItem(row, _COL_URL, url_item)

    def _resize_columns(self) -> None:
        self.table.resizeColumnToContents(_COL_RANK)
        for col in (_COL_VIEWS, _COL_LIKES, _COL_COMMENTS, _COL_SHARES, _COL_ENGAGE, _COL_DURATION, _COL_DATE):
            self.table.resizeColumnToContents(col)
        self.table.setColumnWidth(_COL_HASHTAGS, 180)
        self.table.setColumnWidth(_COL_URL, 280)
        self.table.horizontalHeader().setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)

    def _on_selection_changed(self) -> None:
        self.copy_url_btn.setEnabled(bool(self.table.selectedItems()))

    def copy_selected_url(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        url_item = self.table.item(row, _COL_URL)
        if url_item:
            QApplication.clipboard().setText(url_item.text())
            self.status.setText(self.t("analyze.copied"))

    def export_csv(self) -> None:
        if not self._results:
            return
        default_name = _suggest_filename(self.url_input.text().strip(), self.config.key)
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.t("analyze.export_dialog_title"),
            default_name,
            "CSV Files (*.csv)",
        )
        if not path:
            return
        headers = [self.t(f"analyze.col_{c}") for c in _ANALYZE_COLS]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for i, video in enumerate(self._results, 1):
                writer.writerow([
                    i,
                    video.get("title", ""),
                    video.get("view_count", 0),
                    video.get("like_count", 0),
                    video.get("comment_count", 0),
                    video.get("repost_count", 0),
                    f"{float(video.get('engage_rate') or 0):.1f}%",
                    _fmt_duration(int(video.get("duration") or 0)),
                    video.get("hashtags", ""),
                    _fmt_date(video.get("upload_date", "")),
                    video.get("url", ""),
                ])
        self.status.setText(self.t("analyze.export_saved", path=path))


class ChartDialog(QDialog):
    def __init__(
        self,
        videos: list[dict],
        channel_info: dict,
        url: str,
        platform_key: str,
        theme: str,
        language_getter: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language_getter = language_getter
        self._platform_key = platform_key
        self._url = url
        self._fig = None

        self.setWindowTitle(self.t("analyze.chart_title"))
        self.setMinimumSize(860, 540)
        self.resize(1020, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        self._fig = self._build_figure(videos, channel_info, theme)
        if self._fig is not None:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # type: ignore[import]
            canvas = FigureCanvasQTAgg(self._fig)
            canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout.addWidget(canvas, 1)
        else:
            no_data = QLabel(self.t("analyze.chart_no_dates"))
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_data.setObjectName("helperText")
            layout.addWidget(no_data, 1)

        btn_row = QHBoxLayout()
        save_btn = QPushButton(self.t("analyze.chart_save"))
        save_btn.setObjectName("secondaryButton")
        save_btn.setFixedHeight(36)
        save_btn.clicked.connect(self._save_image)
        btn_row.addWidget(save_btn)

        btn_row.addStretch(1)

        close_btn = QPushButton(self.t("dialog.ok"))
        close_btn.setObjectName("primaryButton")
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def t(self, key: str, **kwargs: str) -> str:
        return translate(self._language_getter(), key, **kwargs)

    def _build_figure(self, videos: list[dict], channel_info: dict, theme: str):
        try:
            from matplotlib.figure import Figure  # type: ignore[import]
        except ImportError:
            return None

        datable = sorted(
            [v for v in videos if len(v.get("upload_date", "")) == 8],
            key=lambda v: v["upload_date"],
        )
        if len(datable) < 2:
            return None

        is_dark = theme == "dark"
        bg        = "#151d2c" if is_dark else "#ffffff"
        ax_bg     = "#101827" if is_dark else "#f8fafc"
        txt       = "#c8d8f0" if is_dark else "#1e293b"
        grid_c    = "#2d3f5a" if is_dark else "#e2e8f0"
        legend_bg = "#1c2a3e" if is_dark else "#f1f5f9"

        dates  = [_fmt_date(v["upload_date"]) for v in datable]
        x      = list(range(len(datable)))
        views  = [v.get("view_count", 0) for v in datable]
        likes  = [v.get("like_count",  0) for v in datable]
        cmts   = [v.get("comment_count", 0) for v in datable]
        shares = [v.get("repost_count", 0) for v in datable]

        win = max(3, len(views) // 5)
        avg_v = [sum(views[max(0, i - win + 1):i + 1]) / min(win, i + 1) for i in range(len(views))]

        fig = Figure(figsize=(10.5, 5.2), dpi=100)
        fig.patch.set_facecolor(bg)
        ax1 = fig.add_subplot(111)
        ax2 = ax1.twinx()
        ax1.set_facecolor(ax_bg)

        ms = max(2, min(5, 80 // len(datable)))

        ax1.plot(x, views, color="#4f7cff", lw=1.8, marker="o", ms=ms,
                 label=self.t("analyze.chart_line_views"))
        ax1.plot(x, avg_v, color="#7ca8e0", lw=3.0, ls="--",
                 label=self.t("analyze.chart_line_avg"))

        ax2.plot(x, likes,  color="#f87171", lw=1.5, marker="o", ms=ms,
                 label=self.t("analyze.chart_line_likes"))
        ax2.plot(x, cmts,   color="#4ade80", lw=1.5, marker="s", ms=ms,
                 label=self.t("analyze.chart_line_comments"))
        ax2.plot(x, shares, color="#fb923c", lw=1.5, marker="^", ms=ms,
                 label=self.t("analyze.chart_line_shares"))

        step = max(1, len(dates) // 12)
        ticks = list(range(0, len(dates), step))
        ax1.set_xticks(ticks)
        ax1.set_xticklabels([dates[i] for i in ticks], rotation=45, ha="right",
                             color=txt, fontsize=8)

        ax1.tick_params(axis="y", colors="#4f7cff", labelsize=9)
        ax1.set_ylabel(self.t("analyze.chart_ylabel_left"), color="#4f7cff", fontsize=10)
        ax2.tick_params(axis="y", colors=txt, labelsize=9)
        ax2.set_ylabel(self.t("analyze.chart_ylabel_right"), color=txt, fontsize=10)
        ax1.set_xlabel(self.t("analyze.chart_xlabel"), color=txt, fontsize=10)

        ax1.grid(True, alpha=0.22, color=grid_c, ls="--")
        ax1.set_axisbelow(True)

        for spine in list(ax1.spines.values()) + list(ax2.spines.values()):
            spine.set_edgecolor(grid_c)

        h1, l1 = ax1.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax1.legend(h1 + h2, l1 + l2,
                   loc="lower center", bbox_to_anchor=(0.5, -0.34), ncol=5,
                   frameon=True, facecolor=legend_bg, edgecolor=grid_c,
                   labelcolor=txt, fontsize=9)

        fig.subplots_adjust(left=0.09, right=0.91, top=0.95, bottom=0.30)
        return fig

    def _save_image(self) -> None:
        if self._fig is None:
            return
        default_name = _suggest_chart_filename(self._url, self._platform_key)
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.t("analyze.chart_save_dialog"),
            default_name,
            "PNG Images (*.png);;JPEG Images (*.jpg)",
        )
        if path:
            self._fig.savefig(path, dpi=150, bbox_inches="tight",
                              facecolor=self._fig.get_facecolor())


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

        self.analyze_panel: AnalysisPanel | None = None
        if self.config.supports_analysis:
            self.analyze_panel = AnalysisPanel(config, service_cls, language_getter, theme_getter)
            self.tabs.addTab(self.analyze_panel, "")

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
        if self.analyze_panel is not None:
            self.tabs.setTabText(2, self.t("tabs.analyze"))
            self.analyze_panel.retranslate()
        if self.cookie_help_btn:
            self.cookie_help_btn.setText(self.t("download.cookie_help_button"))
        self.single_panel.retranslate()
        self.page_panel.retranslate()

    def apply_theme(self) -> None:
        if self.analyze_panel is not None:
            table = self.analyze_panel.table
            table.style().unpolish(table)
            table.style().polish(table)
            if table.viewport():
                table.viewport().update()

    def show_cookie_help(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.t("download.cookie_help_title"))
        platform_key = f"download.cookie_help_message_{self.config.key}"
        msg = self.t(platform_key)
        dialog.setText(msg if msg != platform_key else self.t("download.cookie_help_message"))
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
