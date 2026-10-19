"""验证"写入后自动刷新原理图"的菜单命令。"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

import kicad_ui  # noqa: E402
import sch_sexpr  # noqa: E402

SCHEMATIC = Path(os.environ["TEMP"]) / "erp-kicad-test" / "complex_hierarchy.kicad_sch"


def main() -> int:
    if not SCHEMATIC.is_file():
        print("找不到测试原理图:", SCHEMATIC)
        return 1

    window = kicad_ui.find_schematic_window(str(SCHEMATIC))
    print("窗口:", window)
    if window is None:
        return 1
    print("菜单命令:", kicad_ui.find_revert_command(window.hwnd))

    # 先在磁盘上改一处，模拟插件写入
    text = sch_sexpr.load_file(str(SCHEMATIC))
    symbols = [s for s in sch_sexpr.read_symbols(text) if s.reference and not s.reference.startswith("#")]
    if not symbols:
        print("没有可修改的元件")
        return 1
    edits = [sch_sexpr.plan_set_property(text, symbols[0], "Description",
                                         "自动刷新测试 " + time.strftime("%H%M%S"))]
    sch_sexpr.backup_file(str(SCHEMATIC))
    sch_sexpr.write_file(str(SCHEMATIC), sch_sexpr.apply_edits(text, edits))
    print("已写入 Description 到", symbols[0].reference)

    ok, message = kicad_ui.refresh_schematic(str(SCHEMATIC))
    print("刷新结果:", ok, message)

    time.sleep(0.8)
    dialogs = [w for w in kicad_ui.enum_windows()
               if w.pid == window.pid and w.cls == "#32770" and kicad_ui.user32.IsWindowVisible(w.hwnd)]
    print("remaining dialogs:", len(dialogs))
    for dlg in dialogs:
        print("  dlg hwnd=", dlg.hwnd, "cls=", dlg.cls)
        children = []
        child = kicad_ui.user32.GetWindow(dlg.hwnd, 5)  # GW_CHILD
        while child:
            buf = __import__("ctypes").create_unicode_buffer(128)
            kicad_ui.user32.GetWindowTextW(child, buf, 128)
            cls = __import__("ctypes").create_unicode_buffer(128)
            kicad_ui.user32.GetClassNameW(child, cls, 128)
            cid = kicad_ui.user32.GetDlgCtrlID(child)
            children.append((cid, cls.value, bool(buf.value), len(buf.value)))
            child = kicad_ui.user32.GetWindow(child, 2)  # GW_HWNDNEXT
        print("  children:", children)
    return 0 if ok and not dialogs else 1


if __name__ == "__main__":
    raise SystemExit(main())
