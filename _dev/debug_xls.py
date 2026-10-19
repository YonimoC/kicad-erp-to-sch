import os
import sys
from pathlib import Path

extras = Path(os.environ.get("TEMP", "")) / "erptest" / "Lib" / "site-packages"
if extras.is_dir():
    sys.path.insert(0, str(extras))

import xlrd  # noqa: E402

path = r"D:\_LWH\KiCad Lib\Addons\ERP-To-SCH\原材料基础数据 copy.xls"

for enc in (None, "cp936", "gbk", "utf-16-le"):
    try:
        book = xlrd.open_workbook(path, encoding_override=enc) if enc else xlrd.open_workbook(path)
        sheet = book.sheet_by_index(0)
        print("=== encoding", enc, "===")
        for r in (0, 1, 1074, 2039):
            print(r, [sheet.cell_value(r, c) for c in (0, 1, 2, 7, 8, 19)])
    except Exception as exc:
        print("encoding", enc, "失败:", exc)
