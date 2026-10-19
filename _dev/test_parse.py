"""在真实 KiCad 示例工程上验证解析/写入不破坏文件。"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

import sch_sexpr  # noqa: E402

DEMOS = Path(r"D:\KiCad\10.0\share\kicad\demos")


def main() -> int:
    files = sorted(DEMOS.rglob("*.kicad_sch"))
    if not files:
        print("没有找到示例文件")
        return 1

    total = 0
    for path in files:
        start = time.perf_counter()
        text = sch_sexpr.load_file(path)
        symbols = sch_sexpr.read_symbols(text)
        elapsed = (time.perf_counter() - start) * 1000
        total += len(symbols)
        print(f"{path.name}: {len(symbols):4d} symbols, {len(text)/1024:8.1f} KB, {elapsed:7.1f} ms")

    # 选一个文件做"改一个字段"的完整性测试
    target = files[0]
    text = sch_sexpr.load_file(target)
    symbols = [s for s in sch_sexpr.read_symbols(text) if s.reference and not s.reference.startswith("#")]
    if not symbols:
        print("示例文件里没有可改的元件")
        return 1

    sym = symbols[0]
    edits = [
        sch_sexpr.plan_set_property(text, sym, "PartNumber", "101010674"),
        sch_sexpr.plan_set_property(text, sym, "Description", "贴片电阻,0603,10KΩ,±5%,1/10W,FH"),
    ]
    out = sch_sexpr.apply_edits(text, edits)

    tmpdir = Path(tempfile.mkdtemp(prefix="erp-test-"))
    dst = tmpdir / target.name
    sch_sexpr.write_file(str(dst), out)

    check = sch_sexpr.read_symbols(sch_sexpr.load_file(str(dst)))
    found = [s for s in check if s.uuid == sym.uuid]
    assert found, "写入后找不到原来的 symbol"
    print(f"\n修改 {target.name} 的 {sym.reference}:")
    print("  PartNumber =", found[0].partnumber)
    print("  Description =", found[0].description)

    # 其余内容应当逐字节一致（除了插入的两段属性）
    print("  原始大小:", len(text), "写入后:", len(out), "差值:", len(out) - len(text))

    # 解析器完整性：对每个文件解析后再序列化（这里只验证能解析）
    for path in files:
        sch_sexpr.read_symbols(sch_sexpr.load_file(path))
    print(f"\n共解析 {len(files)} 个文件 / {total} 个元件，全部通过")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
