from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app.locales import translate
from app.updater import UpdateCheck, UpdateError, check_for_update, download_update, is_packaged_app, start_self_update
from app.version import APP_VERSION


def trigger_update_check(language: str) -> None:
    try:
        update_check = check_for_update()
    except UpdateError as exc:
        QMessageBox.warning(
            None,
            translate(language, "update.check_failed_title"),
            translate(language, "update.check_failed_message", error=str(exc)),
        )
        return

    if not update_check.available and not update_check.required:
        QMessageBox.information(
            None,
            translate(language, "update.up_to_date_title"),
            translate(language, "update.up_to_date_message", version=APP_VERSION),
        )
        return

    ensure_update_allowed(language, update_check)


def ensure_update_allowed(language: str, _prefetched: UpdateCheck | None = None) -> bool:
    if _prefetched is None:
        try:
            _prefetched = check_for_update()
        except UpdateError as exc:
            QMessageBox.critical(
                None,
                translate(language, "update.check_failed_title"),
                translate(language, "update.check_failed_message", error=str(exc)),
            )
            return False

    update_check = _prefetched
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
