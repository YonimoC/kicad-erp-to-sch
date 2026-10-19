"""端到端测试：SMD 样图 + 插件件工程。"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-flow-"))

import wx  # noqa: E402

from test_core import SAMPLE  # noqa: E402


def dump(dlg, limit: int = 12) -> None:
    for row in dlg.rows[:limit]:
        print(f"  {row.comp.reference:5s} {row.comp.kind:3s} {row.comp.value:28s} "
              f"{row.status:8s} {row.pn:14s} {row.description[:70]}")


def main() -> int:
    app = wx.App(False)
    import erp_gui

    dlg = erp_gui.ErpToSchDialog(None)

    work = Path(tempfile.mkdtemp(prefix="erp-smd-"))
    (work / "demo.kicad_sch").write_text(SAMPLE, encoding="utf-8")
    print("=== 场景 1：贴片样图 ===")
    dlg.txt_project.SetValue(str(work))
    dlg._scan()
    dump(dlg)

    by_file = {}
    for row in dlg.rows:
        if row.pn and row.status != "已是最新":
            by_file.setdefault(row.comp.file, []).append(row)
    total = 0
    for path, rows in by_file.items():
        total += dlg._write_file(path, rows)
    print(f"写入 {total} 个元件")

    dlg.rows = []
    dlg._scan()
    states = {}
    for row in dlg.rows:
        states[row.status] = states.get(row.status, 0) + 1
    print("重新扫描状态:", states)
    assert states.get("已是最新", 0) + states.get("未自动匹配", 0) == len(dlg.rows), states
    assert states.get("未自动匹配", 0) == 1, states  # 只有 LED

    src = Path(r"D:\KiCad\10.0\share\kicad\demos\complex_hierarchy")
    work2 = Path(tempfile.mkdtemp(prefix="erp-tht-")) / src.name
    shutil.copytree(src, work2)
    print("\n=== 场景 2：插件件工程 ===")
    dlg.txt_project.SetValue(str(work2))
    dlg._scan()
    for row in dlg.rows:
        if row.status not in ("插件件跳过", "未自动匹配"):
            print(f"  {row.comp.reference:6s} {row.comp.kind:3s} {row.comp.value:16s} "
                  f"{row.comp.footprint:50s} {row.status:8s} {row.pn:12s} {row.description[:60]}")
    states = {}
    for row in dlg.rows:
        states[row.status] = states.get(row.status, 0) + 1
    print("状态统计:", states)
    assert states.get("插件件跳过", 0) >= 30, states
    assert states.get("已是最新", 0) == 0, states

    dlg.Destroy()
    app.Destroy()
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
