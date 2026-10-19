"""端到端测试：复制一个示例工程 -> 扫描 -> 匹配 -> 写入文件。"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-flow-"))

import wx  # noqa: E402


def main() -> int:
    src = Path(r"D:\KiCad\10.0\share\kicad\demos\complex_hierarchy")
    tmp = Path(tempfile.mkdtemp(prefix="erp-hier-"))
    work = tmp / src.name
    shutil.copytree(src, work)

    app = wx.App(False)
    import erp_gui
    import sch_sexpr

    dlg = erp_gui.ErpToSchDialog(None)
    dlg.txt_project.SetValue(str(work))
    dlg._scan()

    print(f"找到原理图 {len(dlg.schematic_files)} 个:")
    for path in dlg.schematic_files:
        print("  ", os.path.basename(path))
    print(f"匹配行 {len(dlg.rows)} 条:")
    for row in dlg.rows[:10]:
        print(f"  {row.comp.reference:6s} {row.comp.value:8s} size={row.comp.size!r:8s} "
              f"fp={row.comp.footprint!r} {row.status:6s} {row.pn:14s} {row.description}")

    by_file = {}
    for row in dlg.rows:
        if row.status == "已是最新":
            continue
        by_file.setdefault(row.comp.file, []).append(row)

    total = 0
    for path, rows in by_file.items():
        total += dlg._write_file(path, rows)
    print(f"\n写入 {total} 个元件")

    # 复核：重新扫描，应该变成"已是最新"
    dlg.rows = []
    dlg._scan()
    states = {}
    for row in dlg.rows:
        states[row.status] = states.get(row.status, 0) + 1
    print("重新扫描状态:", states)

    dlg.Destroy()
    app.Destroy()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
