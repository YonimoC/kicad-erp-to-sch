"""
打包脚本：把插件打包为 PCM 可安装的 ZIP。

用法:
    python package.py              → 生成 erp-to-sch.zip
    python package.py --install    → 打包并复制到 KiCad PCM 缓存目录
"""

import argparse
import hashlib
import os
import shutil
import sys
import zipfile
from pathlib import Path

PLUGIN_ID = "erp-to-sch"
ROOT_FILES = ["metadata.json"]
PACK_DIRS = ["plugins", "resources"]
SKIP_DIRS = {"__pycache__", ".git", "_dev"}


def create_zip(source_dir: str, output_zip: str) -> str:
    source = Path(source_dir)
    output = Path(output_zip)

    def iter_entries():
        for name in ROOT_FILES:
            p = source / name
            if p.is_file():
                print(f"  ✓ 添加: {name}")
                yield name, p
            else:
                print(f"  ⚠ 跳过: {name} (文件不存在)")
        for d in PACK_DIRS:
            base = source / d
            if not base.is_dir():
                print(f"  ⚠ 跳过: {d}/ (目录不存在)")
                continue
            for root, subdirs, files in os.walk(base):
                subdirs[:] = [s for s in subdirs if s not in SKIP_DIRS]
                for f in sorted(files):
                    if f.endswith((".pyc", ".bak", ".zip")):
                        continue
                    p = Path(root) / f
                    yield p.relative_to(source).as_posix(), p

    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, filepath in iter_entries():
            zf.write(filepath, arcname)
            count += 1

    print(f"  ✓ 共写入 {count} 个文件")
    sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"\n打包完成: {output}")
    print(f"  文件大小: {output.stat().st_size:,} 字节")
    print(f"  SHA256:   {sha256}")
    return sha256


def install_to_pcm_cache(output_zip: Path):
    home = Path.home()
    for ver in ["10.0", "11.0"]:
        if sys.platform == "win32":
            cache_dir = home / "AppData" / "Local" / "KiCad" / ver / "pcm_cache"
        elif sys.platform == "darwin":
            cache_dir = home / "Library" / "Caches" / "KiCad" / ver / "pcm_cache"
        else:
            cache_dir = home / ".cache" / "kicad" / ver / "pcm_cache"
        if cache_dir.parent.parent.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / output_zip.name
            shutil.copy2(output_zip, dest)
            print(f"  ✓ 已复制到: {dest}")


def main():
    parser = argparse.ArgumentParser(description="打包 KiCad PCM 插件")
    parser.add_argument("--install", action="store_true",
                        help="打包后复制到 KiCad PCM 本地缓存目录")
    args = parser.parse_args()

    script_dir = Path(__file__).parent.resolve()
    output_zip = script_dir / f"{PLUGIN_ID}.zip"

    print("=" * 52)
    print("  KiCad PCM 插件打包工具")
    print("=" * 52)

    create_zip(str(script_dir), str(output_zip))

    if args.install:
        install_to_pcm_cache(output_zip)

    print("\n★ 在 KiCad 中安装：")
    print(f"   PCM → 从文件安装... → 选择: {output_zip}")


if __name__ == "__main__":
    main()
