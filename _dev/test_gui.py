"""GUI 快速冒烟测试：只创建/销毁对话框，不进入事件循环。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
sys.path.insert(0, str(Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"))
os.environ.setdefault("ERP_TO_SCH_SETTINGS_DIR", tempfile.mkdtemp(prefix="erp-gui-"))

import wx  # noqa: E402


def main() -> int:
    app = wx.App(False)
    import erp_gui

    dlg = erp_gui.ErpToSchDialog(None)
    print("对话框创建成功")
    print("列数:", dlg.list.GetColumnCount())
    print("状态栏内容:", dlg.status.GetValue().strip().replace("\n", " | "))
    dlg.Destroy()
    app.Destroy()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
