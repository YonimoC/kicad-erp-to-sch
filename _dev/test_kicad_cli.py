"""用 KiCad 自己的解析器验证我们写入的文件（kicad-cli sch export netlist）。"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

import sch_sexpr  # noqa: E402

KICAD_CLI = Path(r"D:\KiCad\10.0\bin\kicad-cli.exe")
SRC = Path(r"D:\KiCad\10.0\share\kicad\demos\complex_hierarchy\ampli_ht.kicad_sch")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="erp-cli-"))
    dst = work / SRC.name
    shutil.copy2(SRC, dst)

    text = sch_sexpr.load_file(str(dst))
    edits = []
    count = 0
    for symbol in sch_sexpr.read_symbols(text):
        if not symbol.reference or symbol.reference.startswith("#"):
            continue
        edits.append(sch_sexpr.plan_set_property(text, symbol, "PartNumber", f"PN-{count:03d}"))
        edits.append(sch_sexpr.plan_set_property(text, symbol, "Description", f"测试描述 {count}"))
        count += 1
    sch_sexpr.write_file(str(dst), sch_sexpr.apply_edits(text, edits))
    print(f"已给 {count} 个元件写入字段: {dst}")

    out = work / "after.net"
    result = subprocess.run([str(KICAD_CLI), "sch", "export", "netlist",
                             "--format", "kicadsexpr", "-o", str(out), str(dst)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("kicad-cli 返回码:", result.returncode)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        return 1

    content = out.read_text(encoding="utf-8", errors="replace")
    print("网表行数:", content.count("\n"))
    for key in ("PN-000", "测试描述 0", "PN-020"):
        print(f"  包含 {key!r}:", key in content)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
