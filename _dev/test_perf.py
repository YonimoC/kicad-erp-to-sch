"""性能测试：在大原理图上扫描匹配。"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-perf-"))

import wx  # noqa: E402

SRC = Path(r"D:\KiCad\10.0\share\kicad\demos\jetson-agx-thor-baseboard\csi.kicad_sch")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="erp-perf-sch-"))
    shutil.copy2(SRC, work / SRC.name)

    app = wx.App(False)
    import erp_gui

    t0 = time.perf_counter()
    dlg = erp_gui.ErpToSchDialog(None)
    print(f"初始化（含物料表解析）: {time.perf_counter() - t0:.2f}s")

    dlg.txt_project.SetValue(str(work))
    t0 = time.perf_counter()
    dlg._scan()
    print(f"扫描+匹配 {len(dlg.rows)} 个元件: {time.perf_counter() - t0:.2f}s")

    states = {}
    for row in dlg.rows:
        states[row.status] = states.get(row.status, 0) + 1
    print("状态:", states)

    for row in dlg.rows[:8]:
        print(f"  {row.comp.reference:8s} {row.comp.value:10s} {row.comp.size:5s} "
              f"{row.status:8s} {row.pn:14s} {row.description[:60]}")

    dlg.Destroy()
    app.Destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
