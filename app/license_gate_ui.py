"""
License gate UI cho ClipFlow.

ensure_license_allowed() — gọi trong main() trước khi hiện cửa sổ chính.
  - "ok"             → True (tiếp tục)
  - "not_found"      → hiện dialog nhập key, trả True nếu activate thành công
  - "expired"        → hiện thông báo hết hạn, trả False
  - "revoked"        → hiện thông báo bị thu hồi, trả False
  - "device_mismatch"→ hiện thông báo đổi máy, trả False
  - "offline"        → xảy ra khi không có cache + server offline, trả False
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.license_manager import LicenseStatus, activate, validate


class _ActivateDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ClipFlow — Kích hoạt")
        self.setFixedSize(440, 260)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._success = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 32)
        layout.setSpacing(14)

        title = QLabel("Kích hoạt ClipFlow")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        layout.addWidget(title)

        desc = QLabel("Nhập license key để bắt đầu sử dụng.")
        desc.setStyleSheet("color: #5d6b82; font-size: 14px;")
        layout.addWidget(desc)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("CF-XXXX-XXXX-XXXX")
        self._key_input.setFixedHeight(42)
        self._key_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_input.setStyleSheet("font-size: 15px; letter-spacing: 2px;")
        self._key_input.returnPressed.connect(self._on_activate)
        layout.addWidget(self._key_input)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet("color: #dc2626; font-size: 13px;")
        layout.addWidget(self._status_lbl)

        layout.addStretch(1)

        self._btn = QPushButton("Kích hoạt")
        self._btn.setFixedHeight(42)
        self._btn.setStyleSheet(
            "background:#2563eb; color:#fff; border-radius:8px; font-weight:700; font-size:14px;"
        )
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._on_activate)
        layout.addWidget(self._btn)

    def _on_activate(self) -> None:
        key = self._key_input.text().strip()
        if not key:
            self._status_lbl.setText("Vui lòng nhập license key.")
            return

        self._btn.setEnabled(False)
        self._btn.setText("Đang kích hoạt…")
        self._status_lbl.setText("")
        QApplication.processEvents()

        status, _ = activate(key)

        self._btn.setEnabled(True)
        self._btn.setText("Kích hoạt")

        if status == "ok":
            self._success = True
            self.accept()
            return

        self._status_lbl.setText(_ERR_MESSAGES.get(status, _ERR_MESSAGES["_default"]))

    @property
    def success(self) -> bool:
        return self._success


_ERR_MESSAGES: dict[str, str] = {
    "not_found": "Key không tồn tại. Kiểm tra lại hoặc liên hệ hỗ trợ.",
    "expired": "Key đã hết hạn. Vui lòng gia hạn để tiếp tục.",
    "revoked": "Key đã bị thu hồi. Liên hệ hỗ trợ để biết thêm.",
    "device_mismatch": "Key đang được dùng trên máy khác. Liên hệ hỗ trợ để đổi máy.",
    "rate_limited": "Quá nhiều lần thử. Vui lòng chờ 1 phút rồi thử lại.",
    "offline": "Không thể kết nối đến server. Kiểm tra internet và thử lại.",
    "invalid_sig": "Phản hồi server không hợp lệ. Thử lại sau.",
    "_default": "Có lỗi xảy ra. Thử lại sau.",
}

_BLOCK_MESSAGES: dict[str, tuple[str, str]] = {
    "expired": (
        "Gói đã hết hạn",
        "Gói đăng ký của bạn đã hết hạn.\nVui lòng gia hạn để tiếp tục sử dụng ClipFlow.",
    ),
    "revoked": (
        "License bị thu hồi",
        "License key của bạn đã bị thu hồi.\nLiên hệ hỗ trợ để biết thêm chi tiết.",
    ),
    "device_mismatch": (
        "Sai thiết bị",
        "License này đã được kích hoạt trên máy khác.\nLiên hệ hỗ trợ nếu bạn đã đổi máy.",
    ),
    "offline": (
        "Không thể xác thực",
        "ClipFlow không thể kết nối đến server để xác thực license.\nKiểm tra internet và mở lại ứng dụng.",
    ),
    "invalid_sig": (
        "Lỗi xác thực",
        "Phản hồi từ server không hợp lệ.\nVui lòng thử lại sau hoặc liên hệ hỗ trợ.",
    ),
}


def ensure_license_allowed() -> bool:
    """
    Trả True nếu license hợp lệ và app được phép chạy.
    Trả False nếu bị chặn — main() nên gọi sys.exit(0).
    """
    status: LicenseStatus = validate()

    if status == "ok":
        return True

    if status == "not_found":
        dialog = _ActivateDialog()
        dialog.exec()
        return dialog.success

    title, message = _BLOCK_MESSAGES.get(
        status,
        ("Lỗi license", f"Không thể xác thực license ({status}).\nLiên hệ hỗ trợ."),
    )
    box = QMessageBox()
    box.setWindowTitle(title)
    box.setText(message)
    box.setIcon(QMessageBox.Icon.Warning)
    box.exec()
    return False
