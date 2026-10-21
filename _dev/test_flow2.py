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
        print(f"  {row.references:14s} x{row.member_count:<2d} {row.comp.kind:3s} "
              f"{row.comp.value:22s} {row.status:8s} {row.pn:14s} {row.description[:60]}")


def main() -> int:
    app = wx.App(False)
    import erp_gui

    dlg = erp_gui.ErpToSchDialog(None)

    work = Path(tempfile.mkdtemp(prefix="erp-smd-"))
    (work / "demo.kicad_sch").write_text(SAMPLE, encoding="utf-8")
    print("=== 场景 1：贴片样图 ===")
    dlg.txt_project.SetValue(str(work))
    dlg._scan()
    dlg._match_all(interactive=False)
    dump(dlg)

    r_group = next(r for r in dlg.rows if r.comp.reference == "R1")
    print("R1 分组:", r_group.references, "members=", r_group.member_count)
    assert r_group.member_count == 3, r_group.references
    assert len(dlg.rows) == 8, [r.references for r in dlg.rows]

    by_file = {}
    for row in dlg.rows:
        if row.pn and row.status != "已是最新":
            for comp in row.members:
                by_file.setdefault(comp.file, []).append((row, comp))
    total = 0
    for path, entries in by_file.items():
        total += dlg._write_file(path, entries)
    print(f"写入 {total} 个元件")

    dlg.rows = []
    dlg._scan()
    dlg._match_all(interactive=False)
    states = {}
    for row in dlg.rows:
        states[row.status] = states.get(row.status, 0) + 1
    print("重新扫描状态:", states)
    # 已有数据会被核对：除了已是最新，还可能被标记为 非最优 / 料号有误等
    known = ("已是最新", "已匹配", "非最优", "料号有误", "描述不符", "料号不匹配",
             "未自动匹配")
    assert sum(states.get(s, 0) for s in known) == len(dlg.rows), states
    assert len(dlg.rows) == 8, len(dlg.rows)

    src = Path(r"D:\KiCad\10.0\share\kicad\demos\complex_hierarchy")
    work2 = Path(tempfile.mkdtemp(prefix="erp-tht-")) / src.name
    shutil.copytree(src, work2)
    print("\n=== 场景 2：插件件工程 ===")
    dlg.txt_project.SetValue(str(work2))
    dlg._scan()
    dlg._match_all(interactive=False)
    for row in dlg.rows:
        if row.status not in ("插件件跳过", "未自动匹配"):
            print(f"  {row.references:12s} x{row.member_count:<2d} {row.comp.kind:3s} "
                  f"{row.comp.value:16s} {row.comp.footprint:45s} {row.status:8s} {row.pn:12s}")
    states = {}
    for row in dlg.rows:
        states[row.status] = states.get(row.status, 0) + 1
    print("状态统计:", states)
    # 插件元件现在也参与匹配（不再全部跳过）
    assert states.get("插件件跳过", 0) == 0, states
    assert states.get("已匹配", 0) >= 1, states
    assert states.get("已是最新", 0) == 0, states

    dlg.Destroy()
    app.Destroy()
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
