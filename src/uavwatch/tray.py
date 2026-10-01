"""系统托盘 —— 关闭窗口不退出，后台继续跑模型。

用户要的是：叉掉窗口后，搜索和本地模型推理**继续在后台算**，
不然"关掉就等于今天白搜了"。所以：

  · closeEvent 里 hide() 而不是真关
  · 第一次隐藏弹个气泡告诉用户"还在后台跑，图标在托盘里"
  · 托盘菜单：显示主窗口 / 立即搜索 / 真正退出
  · 真要退出走 tray 的"退出"，会先把线程收干净
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

log = logging.getLogger("uavwatch.tray")


def _fallback_icon(color: str = "#95ccad") -> QIcon:
    """没有 ico 文件时现画一个 —— 托盘没图标会直接不可见。"""
    pm = QPixmap(64, 64)
    pm.fill("transparent")
    p = QPainter(pm)
    try:
        from PySide6.QtGui import QBrush, QColor, QPen
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(color)))
        p.setPen(QPen(QColor(color).darker(140), 2))
        p.drawEllipse(6, 6, 52, 52)
        p.setPen(QPen(QColor("#10151F"), 3))
        p.drawLine(20, 32, 44, 32)
        p.drawLine(32, 20, 32, 44)
    finally:
        p.end()
    return QIcon(pm)


class Tray(QObject):
    """托盘图标。"""

    show_window = Signal()
    search_now = Signal()
    quit_app = Signal()

    def __init__(self, window, icon: QIcon | None = None, parent=None):
        super().__init__(parent)
        self.window = window
        self.tray = QSystemTrayIcon(icon if icon and not icon.isNull()
                                    else _fallback_icon(), self)
        self.tray.setToolTip("无人机新规雷达 —— 后台运行中")

        menu = QMenu()
        act_show = QAction("显示主窗口", menu)
        act_show.triggered.connect(self.show_window.emit)
        menu.addAction(act_show)

        act_search = QAction("立即搜索", menu)
        act_search.triggered.connect(self.search_now.emit)
        menu.addAction(act_search)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_app.emit)
        menu.addAction(act_quit)

        # 菜单要挂住，否则被 GC 掉菜单就点不出来了
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger,     # 单击
                      QSystemTrayIcon.DoubleClick):
            self.show_window.emit()

    def show(self) -> None:
        self.tray.show()

    def set_tooltip(self, text: str) -> None:
        self.tray.setToolTip(text[:120])

    def notify(self, title: str, text: str) -> None:
        """弹气泡提示。只在支持的环境下调用。"""
        try:
            if QSystemTrayIcon.supportsMessages():
                self.tray.showMessage(title, text,
                                      QSystemTrayIcon.Information, 4000)
        except Exception as e:                 # noqa: BLE001
            log.debug("托盘气泡失败: %s", e)

    def hide(self) -> None:
        try:
            self.tray.hide()
        except Exception:                      # noqa: BLE001
            pass
