"""通过 Windows 消息让正在运行的 KiCad 重新加载原理图。

KiCad 10 的原理图 IPC API 没有"重新加载文档"命令，
但「文件 → 放弃变更」(`ACTIONS::revert`) 会从磁盘重新读取图纸。
这里用 Win32 枚举窗口/菜单，向原理图窗口发送该菜单命令，并自动确认弹出的对话框。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import time
from typing import Iterable, List, Optional, Sequence, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_COMMAND = 0x0111
IDYES = 6
IDOK = 1
MF_BYPOSITION = 0x400

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.GetMenu.argtypes = [wt.HWND]
user32.GetMenu.restype = wt.HMENU
user32.GetSubMenu.argtypes = [wt.HMENU, ctypes.c_int]
user32.GetSubMenu.restype = wt.HMENU
user32.GetMenuItemCount.argtypes = [wt.HMENU]
user32.GetMenuItemCount.restype = ctypes.c_int
user32.GetMenuItemID.argtypes = [wt.HMENU, ctypes.c_int]
user32.GetMenuItemID.restype = wt.UINT
user32.GetMenuStringW.argtypes = [wt.HMENU, wt.UINT, wt.LPWSTR, ctypes.c_int, wt.UINT]
user32.GetMenuStringW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetDlgCtrlID.argtypes = [wt.HWND]
user32.GetDlgCtrlID.restype = ctypes.c_int
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = wt.LPARAM

#: 「放弃变更 / Revert」在不同语言下的菜单文字
REVERT_LABELS = ("放弃变更", "还原", "Revert", "重新载入", "重新加载", "Reload")


class WindowInfo:
    __slots__ = ("hwnd", "pid", "title", "cls")

    def __init__(self, hwnd: int, pid: int, title: str, cls: str):
        self.hwnd = hwnd
        self.pid = pid
        self.title = title
        self.cls = cls

    def __repr__(self) -> str:  # pragma: no cover
        return f"WindowInfo({self.hwnd}, {self.pid}, {self.title!r}, {self.cls!r})"


def _text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 2)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enum_windows() -> List[WindowInfo]:
    result: List[WindowInfo] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.append(WindowInfo(hwnd, pid.value, _text(hwnd), _class(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    return result


def ancestor_pids() -> List[int]:
    """当前进程的祖先进程（插件进程由 eeschema / kicad 启动）。"""
    pids: List[int] = []
    try:
        import psutil

        proc = psutil.Process()
        for _ in range(5):
            proc = proc.parent()
            if proc is None:
                break
            pids.append(proc.pid)
    except Exception:
        pass
    return pids


def _menu_label(hmenu, pos: int) -> str:
    length = user32.GetMenuStringW(hmenu, pos, None, 0, MF_BYPOSITION)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetMenuStringW(hmenu, pos, buf, length + 2, MF_BYPOSITION)
    text = buf.value
    # 去掉 "&" 和快捷键提示
    return text.split("\t")[0].replace("&", "").strip()


def _find_in_menu(hmenu, labels: Sequence[str], depth: int = 0) -> Optional[Tuple[int, str]]:
    if not hmenu or depth > 2:
        return None
    count = user32.GetMenuItemCount(hmenu)
    for pos in range(count):
        label = _menu_label(hmenu, pos)
        if not label:
            continue
        submenu = user32.GetSubMenu(hmenu, pos)
        if submenu:
            found = _find_in_menu(submenu, labels, depth + 1)
            if found:
                return found
            continue
        cleaned = label.replace(" ", "").rstrip(".。…")
        # 只接受完全匹配，避免误中「从本地历史记录中还原工程...」之类
        if cleaned in labels:
            return user32.GetMenuItemID(hmenu, pos), label
    return None


def find_schematic_window(schematic_path: str = "") -> Optional[WindowInfo]:
    """找到当前原理图编辑器的顶层窗口。"""
    pids = set(ancestor_pids())
    windows = [w for w in enum_windows()
               if user32.IsWindowVisible(w.hwnd) and w.cls.lower().startswith("wxwindow")]

    if pids:
        windows = [w for w in windows if w.pid in pids]
    else:
        windows = [w for w in windows
                   if ("kicad" in w.title.lower()
                       or "原理图编辑器" in w.title
                       or "schematic editor" in w.title.lower())]

    # 必须是带「放弃变更」菜单的原理图窗口（排除 KiCad 工程管理器）
    windows = [w for w in windows if find_revert_command(w.hwnd) is not None]

    stem = os.path.splitext(os.path.basename(schematic_path or ""))[0].lower()
    folder = os.path.basename(os.path.dirname(os.path.abspath(schematic_path))).lower() \
        if schematic_path else ""

    def score(w: WindowInfo) -> int:
        title = w.title.lower()
        value = 0
        if stem and stem in title:
            value += 100
        if folder and folder in title:
            value += 30
        if "原理图编辑器" in w.title or "schematic editor" in title:
            value += 40
        return value

    if not windows:
        return None
    windows.sort(key=score, reverse=True)
    return windows[0]


def find_revert_command(hwnd: int) -> Optional[Tuple[int, str]]:
    hmenu = user32.GetMenu(hwnd)
    if not hmenu:
        return None
    count = user32.GetMenuItemCount(hmenu)
    # 优先在"文件"菜单里找
    for pos in range(count):
        label = _menu_label(hmenu, pos)
        if "文件" in label or label.lower().startswith("file"):
            submenu = user32.GetSubMenu(hmenu, pos)
            found = _find_in_menu(submenu, REVERT_LABELS)
            if found:
                return found
    return _find_in_menu(hmenu, REVERT_LABELS)


def _dialog_windows(pid: int, exclude: Iterable[int]) -> List[WindowInfo]:
    exclude_set = set(exclude)
    result = []
    for win in enum_windows():
        if win.pid != pid or win.hwnd in exclude_set:
            continue
        if not user32.IsWindowVisible(win.hwnd):
            continue
        if win.cls == "#32770":  # 原生 MessageBox / 对话框
            result.append(win)
    return result


def _find_yes_button(dialog: int) -> Optional[Tuple[int, int]]:
    """在对话框中找到「是 / Yes / 确定」按钮，返回 (hwnd, id)。"""
    GW_CHILD, GW_HWNDNEXT = 5, 2
    fallback: Optional[Tuple[int, int]] = None
    child = user32.GetWindow(dialog, GW_CHILD)
    while child:
        cls = _class(child)
        if cls.lower() == "button":
            text = _text(child).replace("&", "").strip()
            ctrl_id = user32.GetDlgCtrlID(child)
            if text.startswith(("是", "Yes", "确定", "OK")):
                return child, ctrl_id
            if fallback is None:
                fallback = (child, ctrl_id)
        child = user32.GetWindow(child, GW_HWNDNEXT)
    return fallback


def _confirm_dialog(hwnd: int) -> bool:
    """点击确认框的「是」。KiCad 用的是 TaskDialog，按钮 ID 为 5100 之类。"""
    button = _find_yes_button(hwnd)
    if button is not None:
        button_hwnd, ctrl_id = button
        user32.SendMessageW(button_hwnd, 0x00F5, 0, 0)  # BM_CLICK
        time.sleep(0.25)
        if not user32.IsWindow(hwnd):
            return True
        user32.PostMessageW(hwnd, WM_COMMAND, ctrl_id, 0)
        time.sleep(0.25)
        if not user32.IsWindow(hwnd):
            return True

    user32.PostMessageW(hwnd, WM_COMMAND, IDYES, 0)
    time.sleep(0.2)
    if user32.IsWindow(hwnd):
        user32.PostMessageW(hwnd, WM_COMMAND, IDOK, 0)
        time.sleep(0.2)
    return not bool(user32.IsWindow(hwnd))


def refresh_schematic(schematic_path: str, auto_confirm: bool = True,
                      timeout: float = 4.0) -> Tuple[bool, str]:
    """让 KiCad 执行"放弃变更"以从磁盘重新加载当前原理图。"""
    window = find_schematic_window(schematic_path)
    if window is None:
        return False, "未找到 KiCad 原理图窗口（可能 API 插件不是从原理图编辑器启动的）。"

    command = find_revert_command(window.hwnd)
    if command is None:
        return False, "未找到『文件 → 放弃变更』菜单项，请手动重新打开原理图。"

    existing = [w.hwnd for w in _dialog_windows(window.pid, [])]
    user32.SetForegroundWindow(window.hwnd)
    time.sleep(0.1)
    user32.PostMessageW(window.hwnd, WM_COMMAND, command[0], 0)

    if not auto_confirm:
        return True, f"已发送『{command[1]}』命令，请在 KiCad 弹出的对话框中点『是』。"

    deadline = time.time() + timeout
    while time.time() < deadline:
        dialogs = _dialog_windows(window.pid, existing)
        if dialogs:
            time.sleep(0.2)
            if _confirm_dialog(dialogs[0].hwnd):
                return True, "已自动重新加载原理图。"
            return True, "已发送重新加载命令，请在弹出的对话框中点『是』。"
        time.sleep(0.15)

    return True, ("已发送『放弃变更』命令，但没有检测到确认框（可能已直接重载）。"
                  "若图纸未更新，请手动执行 文件 → 放弃变更。")
