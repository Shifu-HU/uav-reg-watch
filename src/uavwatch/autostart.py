"""开机自启开关。

用户要求："拉，设个打开软件自动启动"。

做法：写 HKEY_CURRENT_USER\\...\\Run 注册表项。
选 HKCU 而不是 HKLM 的原因 —— HKCU 不需要管理员权限，
也不会弹 UAC。开机自启一个监控小程序，犯不上要管理员。

注意 exe 路径要用引号包起来：路径里有空格（"C:\\Program Files\\..."），
不包的话 Windows 会把空格前后当成两个参数，启动直接失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "UAVRegWatch"


def _exe_path() -> str:
    """拿到应该自启的可执行文件路径。

    打包后 sys.executable 就是 exe；源码运行时是 python.exe，
    这时改成 pythonw.exe + app_mine.py，否则开机弹黑框。
    """
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return str(exe)
    # 源码模式：用 pythonw 避免控制台窗口
    pyw = exe.with_name("pythonw.exe")
    script = Path(__file__).resolve().parent.parent.parent / "app_mine.py"
    if pyw.exists() and script.exists():
        return f'"{pyw}" "{script}"'
    return str(exe)


def _command() -> str:
    p = _exe_path()
    # 已经带引号的（源码模式拼好的）不再重复加
    if p.startswith('"'):
        return p
    return f'"{p}"'


def is_enabled() -> bool:
    """当前是否已设置开机自启。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> tuple[bool, str]:
    """设置开机自启。返回 (是否成功, 说明)。"""
    try:
        import winreg
        cmd = _command()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.SetValueEx(k, _APP_NAME, 0, winreg.REG_SZ, cmd)
        return True, f"已设置开机自启：{cmd}"
    except OSError as e:
        return False, f"设置失败：{e}"


def disable() -> tuple[bool, str]:
    """取消开机自启。返回 (是否成功, 说明)。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            access=winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _APP_NAME)
        return True, "已取消开机自启"
    except FileNotFoundError:
        return True, "本来就没设置"
    except OSError as e:
        return False, f"取消失败：{e}"


def current_command() -> str:
    """读取当前注册的命令（用于界面上显示）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _APP_NAME)
            return str(val)
    except (FileNotFoundError, OSError):
        return ""
