"""单实例守卫 —— 防止重复启动。

用户报的现象：双击图标又开一个窗口，两个进程同时搜同一批关键词、
同时调本地模型，机器直接卡死。

用 QLocalServer 做进程间握手，比文件锁可靠：
  · 进程崩了系统自动回收 socket，不会留下死锁文件
  · 能顺带把已有窗口"唤醒"（恢复显示并置顶）
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("uavwatch.single")

# 服务名。带用户名避免多用户登录时互相顶掉。
import getpass
try:
    _USER = getpass.getuser()
except Exception:                              # noqa: BLE001
    _USER = "default"
SERVER_NAME = f"UAVRegWatchMine-{_USER}"

_WAKE = b"WAKE"


class SingleInstance(QObject):
    """单实例守卫。

    用法::

        guard = SingleInstance()
        if not guard.try_acquire():
            guard.notify_existing()      # 让已有窗口弹出来
            sys.exit(0)
        guard.wake.connect(window.raise_from_tray)
    """

    wake = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        """抢占用。返回 True 表示本进程是唯一实例。"""
        # 先试着连一下 —— 连得上说明已经有实例了
        probe = QLocalSocket()
        probe.connectToServer(SERVER_NAME)
        if probe.waitForConnected(300):
            probe.disconnectFromServer()
            return False
        probe.abort()

        # 连不上。可能是残留的 server 文件（上次崩溃没清理），
        # 也可能是真的没有实例。removeServer 后重建即可 ——
        # 如果真有实例在跑，上面的 probe 早就连上了。
        QLocalServer.removeServer(SERVER_NAME)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)
        if not self.server.listen(SERVER_NAME):
            # 极少数情况下 listen 仍失败（权限/占用），
            # 不阻塞启动 —— 多开一个总比打不开强。
            log.warning("单实例监听失败: %s", self.server.errorString())
            return True
        return True

    def notify_existing(self) -> bool:
        """告诉已有实例"用户又点了一次图标"，让它把窗口弹出来。"""
        s = QLocalSocket()
        s.connectToServer(SERVER_NAME)
        if not s.waitForConnected(500):
            return False
        s.write(_WAKE)
        s.flush()
        s.waitForBytesWritten(500)
        s.disconnectFromServer()
        return True

    def _on_connection(self) -> None:
        if self.server is None:
            return
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            if conn is None:
                continue
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(conn.deleteLater)
            # readyRead 常常在这之后才触发，但数据可能**已经到了**。
            # 不主动读一次的话，唤醒信号就丢了（实测踩过：
            # 第二个实例 notify 返回 True，主窗口却纹丝不动）。
            self._read(conn)
            # 数据还没到就等一小会儿 —— 本地 socket 几乎瞬间就到。
            if not getattr(conn, "_uav_read", False):
                try:
                    conn.waitForReadyRead(600)
                    self._read(conn)
                except Exception:              # noqa: BLE001
                    pass

    def _read(self, conn) -> None:
        try:
            if conn.bytesAvailable() <= 0:
                return
            data = bytes(conn.readAll())
        except Exception:                      # noqa: BLE001
            return
        conn._uav_read = True                  # 标记已读，避免重复发信号
        if data.startswith(_WAKE) or not data:
            self.wake.emit()
