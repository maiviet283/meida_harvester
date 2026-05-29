from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.locales import translate
from app.platforms.registry import PLATFORM_MODULES
from app.single_instance import SingleInstanceGuard
from app.themes import build_stylesheet, get_theme
from app.update_ui import ensure_update_allowed, trigger_update_check
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

        footer_row = QHBoxLayout()
        footer_row.setSpacing(8)
        footer_row.setContentsMargins(0, 0, 0, 0)

        self.footer = QLabel()
        self.footer.setObjectName("sidebarFooter")
        footer_row.addWidget(self.footer, 1)

        self.update_btn = QPushButton()
        self.update_btn.setObjectName("sidebarUpdateButton")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.check_for_updates)
        footer_row.addWidget(self.update_btn)

        sidebar_layout.addLayout(footer_row)

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

    def check_for_updates(self) -> None:
        trigger_update_check(self.language)

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
        self.footer.setText(f"ClipFlow {APP_VERSION}")
        self.update_btn.setText(self.t("app.check_update"))
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
        no_btn = dialog.addButton(self.t("dialog.no"), QMessageBox.ButtonRole.NoRole)
        yes_btn.setFixedSize(76, 28)
        no_btn.setFixedSize(76, 28)
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


def _find_icon() -> Path | None:
    if getattr(sys, "frozen", False):
        candidate = Path(sys._MEIPASS) / "assets" / "icon.ico"  # type: ignore[attr-defined]
    else:
        candidate = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
    return candidate if candidate.exists() else None


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    icon_path = _find_icon()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))

    guard = SingleInstanceGuard()
    if not guard.is_primary:
        sys.exit(0)

    from app.license_gate_ui import ensure_license_allowed
    if not ensure_license_allowed():
        sys.exit(0)

    if not ensure_update_allowed("vi"):
        sys.exit(0)

    window = MainWindow()
    guard.bind_window(window)
    window.showMaximized()
    sys.exit(app.exec())
