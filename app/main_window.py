from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.locales import translate
from app.platforms.registry import PLATFORM_MODULES
from app.themes import build_stylesheet, get_theme
from app.updater import UpdateError, check_for_update, download_update, is_packaged_app, start_self_update
from app.version import APP_VERSION


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.language = "vi"
        self.theme_name = "light"
        self.pages_by_platform: list[QWidget] = []

        self.setMinimumSize(1020, 650)
        self.resize(1120, 720)
        self.build_ui()
        self.apply_theme()
        self.retranslate()

    def build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(252)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 22, 18, 18)
        sidebar_layout.setSpacing(18)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        self.app_icon = QLabel()
        self.app_icon.setFixedSize(40, 40)
        brand_row.addWidget(self.app_icon)

        self.brand = QLabel()
        self.brand.setObjectName("brand")
        brand_row.addWidget(self.brand, 1)
        sidebar_layout.addLayout(brand_row)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("sidebarSubtitle")
        self.subtitle.setWordWrap(True)
        sidebar_layout.addWidget(self.subtitle)

        self.menu = QListWidget()
        self.menu.setObjectName("platformMenu")
        self.menu.setFrameShape(QFrame.Shape.NoFrame)
        self.menu.setSpacing(8)
        for module in PLATFORM_MODULES:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, module.config.key)
            self.menu.addItem(item)
        self.menu.currentRowChanged.connect(self.change_platform)
        sidebar_layout.addWidget(self.menu, 1)

        self.footer = QLabel()
        self.footer.setObjectName("sidebarFooter")
        sidebar_layout.addWidget(self.footer)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("topToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(28, 18, 28, 0)
        toolbar_layout.setSpacing(10)
        toolbar_layout.addStretch(1)

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("toolbarButton")
        self.theme_btn.clicked.connect(self.toggle_theme)
        toolbar_layout.addWidget(self.theme_btn)

        self.language_btn = QPushButton()
        self.language_btn.setObjectName("toolbarButton")
        self.language_btn.clicked.connect(self.toggle_language)
        toolbar_layout.addWidget(self.language_btn)

        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for module in PLATFORM_MODULES:
            page = module.create_page(self.get_language, self.get_theme_name)
            self.pages_by_platform.append(page)
            self.pages.addWidget(page)

        content_layout.addWidget(toolbar)
        content_layout.addWidget(self.pages, 1)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.menu.setCurrentRow(0)

    def get_language(self) -> str:
        return self.language

    def get_theme_name(self) -> str:
        return self.theme_name

    def t(self, key: str, **kwargs: str) -> str:
        return translate(self.language, key, **kwargs)

    def change_platform(self, row: int) -> None:
        if row >= 0:
            self.pages.setCurrentIndex(row)

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.apply_theme()
        self.retranslate()

    def toggle_language(self) -> None:
        self.language = "en" if self.language == "vi" else "vi"
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(self.t("app.title"))
        self.brand.setText(self.t("app.brand"))
        self.subtitle.setText(self.t("app.subtitle"))
        self.footer.setText(self.t("app.phase"))
        self.theme_btn.setText(
            self.t("app.theme_dark") if self.theme_name == "light" else self.t("app.theme_light")
        )
        self.language_btn.setText(
            self.t("app.language_en") if self.language == "vi" else self.t("app.language_vi")
        )
        for index, module in enumerate(PLATFORM_MODULES):
            self.menu.item(index).setText(self.t(f"platforms.{module.config.key}.name"))
        for page in self.pages_by_platform:
            page.retranslate()

    def closeEvent(self, event) -> None:  # noqa: N802
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.t("dialog.exit_title"))
        dialog.setText(self.t("dialog.exit_message"))
        dialog.setIcon(QMessageBox.Icon.Question)
        yes_btn = dialog.addButton(self.t("dialog.yes"), QMessageBox.ButtonRole.YesRole)
        dialog.addButton(self.t("dialog.no"), QMessageBox.ButtonRole.NoRole)
        dialog.setDefaultButton(dialog.buttons()[-1])
        dialog.exec()
        if dialog.clickedButton() == yes_btn:
            event.accept()
        else:
            event.ignore()

    def apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(build_stylesheet(self.theme_name))
        self.update_icon()
        for page in self.pages_by_platform:
            page.apply_theme()

    def update_icon(self) -> None:
        theme = get_theme(self.theme_name)
        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(theme["icon_bg"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(1, 1, 38, 38, 10, 10)

        pen = QPen(QColor(theme["icon_mark"]), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(13, 13, 13, 27)
        painter.drawLine(13, 13, 27, 20)
        painter.drawLine(13, 27, 27, 20)
        painter.drawLine(28, 12, 28, 28)
        painter.end()

        self.app_icon.setPixmap(pixmap)


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    if not handle_startup_update("vi"):
        sys.exit(0)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


def handle_startup_update(language: str) -> bool:
    try:
        update_check = check_for_update()
    except UpdateError:
        return True

    if not update_check.available and not update_check.required:
        return True

    info = update_check.info
    dialog = QMessageBox()
    dialog.setIcon(QMessageBox.Icon.Information)
    dialog.setWindowTitle(
        translate(language, "update.required_title" if update_check.required else "update.available_title")
    )
    dialog.setText(
        info.message
        or translate(
            language,
            "update.message",
            current=APP_VERSION,
            latest=info.latest_version,
        )
    )
    update_btn = dialog.addButton(translate(language, "update.update_now"), QMessageBox.ButtonRole.AcceptRole)
    if update_check.required:
        dialog.addButton(translate(language, "update.exit"), QMessageBox.ButtonRole.RejectRole)
    else:
        dialog.addButton(translate(language, "update.later"), QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(update_btn)
    dialog.exec()

    if dialog.clickedButton() != update_btn:
        return not update_check.required

    if not is_packaged_app():
        QMessageBox.warning(
            None,
            translate(language, "update.dev_mode_title"),
            translate(language, "update.dev_mode_message"),
        )
        return not update_check.required

    progress_dialog = QProgressDialog(translate(language, "update.downloading"), None, 0, 100)
    progress_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress_dialog.setCancelButton(None)
    progress_dialog.setMinimumDuration(0)
    progress_dialog.show()

    def update_progress(downloaded: int, total: int) -> None:
        if total:
            progress_dialog.setValue(min(100, int(downloaded * 100 / total)))
        QApplication.processEvents()

    try:
        archive_path = download_update(info, update_progress)
        progress_dialog.setValue(100)
        start_self_update(archive_path)
    except UpdateError as exc:
        progress_dialog.close()
        QMessageBox.critical(
            None,
            translate(language, "update.failed_title"),
            translate(language, "update.failed_message", error=str(exc)),
        )
        return not update_check.required

    progress_dialog.close()
    QMessageBox.information(
        None,
        translate(language, "update.restart_title"),
        translate(language, "update.restart_message"),
    )
    return False
