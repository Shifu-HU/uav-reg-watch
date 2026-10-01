"""把 Windows 系统标题栏染成跟应用顶栏一致的颜色。

不做的话，系统标题栏跟着 Windows 主题走（深色系统就是黑的），
直接压在浅色的应用顶栏上，看起来像两个界面拼在一起。
用户报的"软件标题与上方栏背景对不上"就是这个。

用 DWM 的 DWMWA_CAPTION_COLOR(35)，Windows 11 22000+ 才支持。
旧系统或非 Windows 上静默跳过 —— 这只是观感优化，不能因为它
失败就让程序起不来。
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("uavwatch.winutil")

DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_USE_IMMERSIVE_DARK_MODE = 20

# DWMWA_COLOR_DEFAULT / DWMWA_COLOR_NONE，用于告诉 DWM "别用系统主题色"
COLOR_DEFAULT = 0xFFFFFFFF


def _colorref(hex_color: str) -> int:
    """#RRGGBB -> COLORREF。

    注意 COLORREF 是 0x00BBGGRR（BGR 顺序），不是 RGB。
    传反了会得到互补色（浅绿变成粉红），很难一眼看出。
    """
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return COLOR_DEFAULT
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return COLOR_DEFAULT
    return (b << 16) | (g << 8) | r


def _dwm():
    """拿到声明好签名的 dwmapi。

    必须显式声明 argtypes —— 不声明时 ctypes 默认按 C int 传参，
    64 位下 HWND 会被截断成 32 位，DwmSetWindowAttribute 拿到
    无效句柄直接返回 E_HANDLE，表现为"调用了但没效果"。
    """
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        return dwm
    except Exception:                          # noqa: BLE001
        return None


def set_caption_color(hwnd: int, bg: str, fg: str = "",
                      dark: bool = False) -> bool:
    """把窗口标题栏刷成 bg 色。返回是否至少有一项成功。"""
    dwm = _dwm()
    if dwm is None or not hwnd:
        return False

    def _set(attr: int, val: int) -> bool:
        v = ctypes.c_uint(val & 0xFFFFFFFF)
        try:
            hr = dwm.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
            return hr == 0
        except Exception:                      # noqa: BLE001
            return False

    ok = _set(DWMWA_CAPTION_COLOR, _colorref(bg))
    if fg:
        _set(DWMWA_TEXT_COLOR, _colorref(fg))
    _set(DWMWA_BORDER_COLOR, _colorref(bg))
    # 深色主题要同步告诉 DWM，否则标题栏文字/按钮图标还是浅色系的
    _set(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
    return ok


def apply_titlebar(window, bg: str, fg: str = "",
                   dark: bool = False) -> None:
    """给一个 QWidget/QMainWindow 应用标题栏配色。

    Qt 的 winId() 在窗口 show() 之前可能还没稳定，
    所以调用方应该在 show() 之后调这个。
    """
    try:
        set_caption_color(int(window.winId()), bg, fg, dark)
    except Exception as e:                     # noqa: BLE001
        log.debug("标题栏着色失败: %s", e)
