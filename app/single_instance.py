from __future__ import annotations

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QMainWindow

_SERVER_KEY = "ClipFlow-SingleInstance-v1"


class SingleInstanceGuard:
    """
    Đảm bảo chỉ có một instance app chạy.
    - Instance đầu tiên: tạo server, chạy bình thường.
    - Instance thứ hai: gửi tín hiệu "activate" tới instance đầu rồi thoát.
    Instance đầu nhận tín hiệu → đưa cửa sổ lên foreground.
    """

    def __init__(self) -> None:
        self._server: QLocalServer | None = None
        self._is_primary = self._try_become_primary()

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def _try_become_primary(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(_SERVER_KEY)
        if socket.waitForConnected(500):
            socket.write(b"activate")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return False

        QLocalServer.removeServer(_SERVER_KEY)
        self._server = QLocalServer()
        self._server.listen(_SERVER_KEY)
        return True

    def bind_window(self, window: QMainWindow) -> None:
        if self._server is None:
            return
        self._server.newConnection.connect(lambda: self._on_new_connection(window))

    def _on_new_connection(self, window: QMainWindow) -> None:
        while self._server and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.disconnectFromServer()
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()
