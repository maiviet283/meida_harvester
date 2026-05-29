from __future__ import annotations


THEMES = {
    "light": {
        "app_bg": "#eef3f8",
        "surface": "#ffffff",
        "surface_alt": "#f7fafc",
        "sidebar_bg": "#101624",
        "sidebar_border": "#233046",
        "sidebar_text": "#dbe7f3",
        "sidebar_muted": "#92a3b8",
        "sidebar_hover": "#1c2738",
        "text": "#101828",
        "muted": "#5d6b82",
        "subtle": "#d8e1ea",
        "line": "#cfdae6",
        "primary": "#2563eb",
        "primary_hover": "#1d4ed8",
        "primary_text": "#ffffff",
        "secondary": "#e7edf5",
        "secondary_hover": "#d7e0ec",
        "cookie_help_bg": "#fff4cf",
        "cookie_help_hover": "#ffe7a3",
        "cookie_help_text": "#684a00",
        "tab_bg": "#dfe7f1",
        "tab_selected": "#ffffff",
        "input_bg": "#fbfdff",
        "input_focus": "#ffffff",
        "danger": "#dc2626",
        "shadow": "rgba(15, 23, 42, 0.10)",
        "dialog_bg": "#ffffff",
        "dialog_text": "#101828",
        "success": "#16a34a",
        "warning": "#f59e0b",
        "icon_bg": "#ffffff",
        "icon_mark": "#2563eb",
    },
    "dark": {
        "app_bg": "#0c111d",
        "surface": "#151d2c",
        "surface_alt": "#101827",
        "sidebar_bg": "#080d16",
        "sidebar_border": "#1d2939",
        "sidebar_text": "#e6edf7",
        "sidebar_muted": "#8ea0b7",
        "sidebar_hover": "#172033",
        "text": "#eef4ff",
        "muted": "#a7b4c7",
        "subtle": "#273449",
        "line": "#344258",
        "primary": "#4f7cff",
        "primary_hover": "#6d92ff",
        "primary_text": "#ffffff",
        "secondary": "#243149",
        "secondary_hover": "#31415e",
        "cookie_help_bg": "#3a2f14",
        "cookie_help_hover": "#4d3d18",
        "cookie_help_text": "#fde68a",
        "tab_bg": "#1c2638",
        "tab_selected": "#273652",
        "input_bg": "#101827",
        "input_focus": "#141f33",
        "danger": "#f87171",
        "shadow": "rgba(0, 0, 0, 0.22)",
        "dialog_bg": "#151d2c",
        "dialog_text": "#eef4ff",
        "success": "#22c55e",
        "warning": "#fbbf24",
        "icon_bg": "#111827",
        "icon_mark": "#6d92ff",
    },
}


def get_theme(name: str) -> dict[str, str]:
    return THEMES.get(name, THEMES["light"])


def build_stylesheet(theme_name: str) -> str:
    theme = get_theme(theme_name)
    return f"""
        QWidget#root {{
            background: {theme["app_bg"]};
            color: {theme["text"]};
            font-family: Segoe UI, Arial, sans-serif;
        }}
        QFrame#sidebar {{
            background: {theme["sidebar_bg"]};
            border-right: 1px solid {theme["sidebar_border"]};
        }}
        QFrame#topToolbar {{
            background: {theme["app_bg"]};
        }}
        QLabel#brand {{
            color: {theme["primary_text"]};
            font-size: 30px;
            font-weight: 800;
        }}
        QLabel#sidebarSubtitle,
        QLabel#sidebarFooter {{
            color: {theme["sidebar_muted"]};
            font-size: 13px;
        }}
        QListWidget#platformMenu {{
            background: transparent;
            color: {theme["sidebar_text"]};
            outline: none;
        }}
        QListWidget#platformMenu::item {{
            padding: 13px 14px;
            border-radius: 8px;
            font-size: 15px;
        }}
        QListWidget#platformMenu::item:hover {{
            background: {theme["sidebar_hover"]};
            color: {theme["primary_text"]};
        }}
        QListWidget#platformMenu::item:selected {{
            background: {theme["primary"]};
            color: {theme["primary_text"]};
            font-weight: 700;
        }}
        QLabel#pageTitle {{
            color: {theme["text"]};
            font-size: 34px;
            font-weight: 800;
        }}
        QLabel#pageDescription {{
            color: {theme["muted"]};
            font-size: 15px;
        }}
        QLabel#sectionLabel {{
            color: {theme["muted"]};
            font-size: 12px;
            font-weight: 750;
            text-transform: uppercase;
        }}
        QTabWidget::pane {{
            border: 0;
            top: 10px;
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: {theme["tab_bg"]};
            color: {theme["muted"]};
            padding: 12px 24px;
            border-radius: 18px;
            margin-right: 10px;
            margin-bottom: 10px;
            min-width: 128px;
            font-weight: 650;
        }}
        QTabBar::tab:hover {{
            background: {theme["secondary_hover"]};
            color: {theme["text"]};
        }}
        QTabBar::tab:selected {{
            background: {theme["primary"]};
            color: {theme["primary_text"]};
        }}
        QFrame#downloadPanel {{
            background: {theme["surface"]};
            border: 1px solid {theme["line"]};
            border-radius: 8px;
        }}
        QLabel#panelTitle {{
            color: {theme["text"]};
            font-size: 22px;
            font-weight: 800;
        }}
        QLabel#helperText {{
            color: {theme["muted"]};
            font-size: 14px;
        }}
        QLineEdit,
        QTextEdit {{
            background: {theme["input_bg"]};
            border: 1px solid {theme["line"]};
            border-radius: 8px;
            padding: 9px 11px;
            color: {theme["text"]};
            selection-background-color: {theme["primary"]};
            selection-color: {theme["primary_text"]};
        }}
        QLineEdit:focus,
        QTextEdit:focus {{
            border-color: {theme["primary"]};
            background: {theme["input_focus"]};
        }}
        QComboBox#filterCombo {{
            background: {theme["input_bg"]};
            border: 1px solid {theme["line"]};
            border-radius: 8px;
            padding: 8px 11px;
            color: {theme["text"]};
        }}
        QComboBox#filterCombo:hover {{
            border-color: {theme["primary"]};
        }}
        QComboBox#filterCombo::drop-down {{
            border: 0;
            width: 34px;
        }}
        QComboBox#filterCombo QAbstractItemView {{
            background: {theme["surface"]};
            color: {theme["text"]};
            border: 1px solid {theme["line"]};
            selection-background-color: {theme["primary"]};
            selection-color: {theme["primary_text"]};
        }}
        QProgressBar {{
            background: {theme["secondary"]};
            border: 1px solid {theme["line"]};
            border-radius: 8px;
            color: {theme["text"]};
            min-height: 18px;
            text-align: center;
            font-weight: 700;
        }}
        QProgressBar::chunk {{
            background: {theme["primary"]};
            border-radius: 8px;
        }}
        QLabel#statusText {{
            color: {theme["muted"]};
            font-size: 14px;
        }}
        QLabel#savePathText {{
            color: {theme["muted"]};
            font-size: 13px;
        }}
        QPushButton {{
            border: 0;
            border-radius: 8px;
            padding: 10px 16px;
            font-weight: 750;
        }}
        QPushButton#primaryButton {{
            background: {theme["primary"]};
            color: {theme["primary_text"]};
        }}
        QPushButton#primaryButton:hover {{
            background: {theme["primary_hover"]};
        }}
        QPushButton#secondaryButton {{
            background: {theme["secondary"]};
            color: {theme["text"]};
        }}
        QPushButton#secondaryButton:hover {{
            background: {theme["secondary_hover"]};
        }}
        QPushButton#tabHelpButton {{
            background: {theme["cookie_help_bg"]};
            color: {theme["cookie_help_text"]};
            border-radius: 18px;
            padding: 12px 18px;
            margin-bottom: 10px;
            min-height: 18px;
            font-weight: 650;
        }}
        QPushButton#tabHelpButton:hover {{
            background: {theme["cookie_help_hover"]};
        }}
        QPushButton#toolbarButton {{
            background: {theme["surface"]};
            color: {theme["text"]};
            border: 1px solid {theme["line"]};
        }}
        QPushButton#toolbarButton:hover {{
            background: {theme["secondary"]};
        }}
        QMessageBox {{
            background: {theme["dialog_bg"]};
            color: {theme["dialog_text"]};
        }}
        QMessageBox QLabel {{
            color: {theme["dialog_text"]};
            font-size: 14px;
        }}
        QMessageBox QPushButton {{
            background: {theme["secondary"]};
            color: {theme["text"]};
            padding: 4px 14px;
            min-width: 76px;
            min-height: 24px;
            max-height: 28px;
        }}
        QMessageBox QPushButton:hover {{
            background: {theme["secondary_hover"]};
        }}
    """
