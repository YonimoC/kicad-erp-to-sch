"""枚举 KiCad 窗口与菜单，用于实现"自动刷新原理图"。"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
from pathlib import Path

_extra = Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"
if _extra.is_dir() and str(_extra) not in sys.path:
    sys.path.insert(0, str(_extra))

user32 = ctypes.WinDLL("user32", use_last_error=True)

MF_BYPOSITION = 0x400
MF_BYCOMMAND = 0x0


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enum_top_windows():
    result = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            result.append((hwnd, pid.value))
        return True

    user32.EnumWindows(callback, 0)
    return result


def menu_items(hwnd: int):
    hmenu = user32.GetMenu(hwnd)
    if not hmenu:
        return []
    items = []
    count = user32.GetMenuItemCount(hmenu)
    for pos in range(count):
        length = user32.GetMenuStringW(hmenu, pos, None, 0, MF_BYPOSITION)
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.GetMenuStringW(hmenu, pos, buf, length + 2, MF_BYPOSITION)
        sub = user32.GetSubMenu(hmenu, pos)
        item_id = user32.GetMenuItemID(hmenu, pos)
        items.append((buf.value, item_id, sub))
    return items


def dump_menu(hmenu: int, depth: int = 0, limit: int = 200) -> None:
    count = user32.GetMenuItemCount(hmenu)
    for pos in range(min(count, limit)):
        length = user32.GetMenuStringW(hmenu, pos, None, 0, MF_BYPOSITION)
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.GetMenuStringW(hmenu, pos, buf, length + 2, MF_BYPOSITION)
        item_id = user32.GetMenuItemID(hmenu, pos)
        state = user32.GetMenuState(hmenu, pos, MF_BYPOSITION)
        sub = user32.GetSubMenu(hmenu, pos)
        flags = []
        if state & 0x1:
            flags.append("grayed")
        if state & 0x2:
            flags.append("disabled")
        print("  " * depth + f"- {buf.value!r} id={item_id} {','.join(flags)}")
        if sub:
            dump_menu(sub, depth + 1)


def main() -> int:
    for hwnd, pid in enum_top_windows():
        title = window_text(hwnd)
        cls = class_name(hwnd)
        if "kicad" not in title.lower() and "wx" not in cls.lower():
            continue
        print(f"\n窗口 hwnd={hwnd} pid={pid} class={cls!r} title={title!r}")
        for text, item_id, sub in menu_items(hwnd):
            print(f"  菜单: {text!r} id={item_id} sub={bool(sub)}")
            if sub and any(k in text for k in ("文件", "File")):
                dump_menu(sub, 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
