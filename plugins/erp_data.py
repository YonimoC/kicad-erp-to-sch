"""ERP 原材料基础数据读取（.xls / .xlsx）+ 本地缓存。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 表头名 -> 逻辑字段
COLUMN_NAMES = {
    "pn": ("物料编码", "物料编码 ", "料号", "PartNumber"),
    "name": ("物料名称",),
    "spec": ("规格", "规格型号", "描述"),
    "disabled": ("禁用",),
    "brand": ("品牌", "制造商"),
    "stock": ("库存", "库存数量"),
    "category": ("分类名称",),
}

#: 没有表头时的固定列序（当前 ERP 导出表，1 基）
FALLBACK_INDEX = {"pn": 0, "name": 1, "spec": 2, "disabled": 6, "brand": 7,
                  "stock": 19, "category": 17}

CACHE_VERSION = 2


@dataclass
class Material:
    """一行 ERP 物料。"""

    pn: str
    name: str
    spec: str
    brand: str
    stock: float
    disabled: bool
    category: str = ""

    @property
    def text(self) -> str:
        """用于关键字匹配的合并文本。"""
        return f"{self.name} {self.spec}"

    def to_row(self) -> List[Any]:
        return [self.pn, self.name, self.spec, self.brand, self.stock,
                self.disabled, self.category]

    @staticmethod
    def from_row(row: Sequence[Any]) -> "Material":
        return Material(
            pn=str(row[0] or "").strip(),
            name=str(row[1] or "").strip(),
            spec=str(row[2] or "").strip(),
            brand=str(row[3] or "").strip(),
            stock=_to_float(row[4]),
            disabled=_to_bool(row[5]),
            category=str(row[6] or "").strip(),
        )


def _to_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "是", "禁用"}


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_xls(path: str) -> List[List[Any]]:
    import xlrd

    def header_is_chinese(sheet) -> bool:
        if sheet.nrows == 0:
            return False
        text = "".join(str(sheet.cell_value(0, c)) for c in range(min(sheet.ncols, 6)))
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    # ERP 导出的 .xls 常常缺少 CODEPAGE 记录，xlrd 默认会退化成 iso-8859-1，
    # 中文全部乱码。先按 GBK(cp936) 试；不行再用默认编码。
    sheet = None
    for kwargs in ({"encoding_override": "cp936"}, {}):
        try:
            book = xlrd.open_workbook(path, **kwargs)
            candidate = book.sheet_by_index(0)
            if header_is_chinese(candidate):
                sheet = candidate
                break
            if not kwargs:
                sheet = candidate
        except Exception:
            continue
    if sheet is None:
        book = xlrd.open_workbook(path)
        sheet = book.sheet_by_index(0)

    rows: List[List[Any]] = []
    for r in range(sheet.nrows):
        rows.append([sheet.cell_value(r, c) for c in range(sheet.ncols)])
    return rows


def _read_xlsx(path: str) -> List[List[Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        return [list(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def read_table(path: str) -> List[Material]:
    """读取 ERP 物料表（第一张工作表）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xls":
        rows = _read_xls(path)
    elif ext in (".xlsx", ".xlsm"):
        rows = _read_xlsx(path)
    else:
        raise ValueError(f"不支持的物料表格式: {ext}")

    if not rows:
        return []

    header = [_to_text(v) for v in rows[0]]
    index: Dict[str, int] = {}
    for key, names in COLUMN_NAMES.items():
        for i, title in enumerate(header):
            if title in names:
                index[key] = i
                break
        else:
            index[key] = FALLBACK_INDEX[key]

    materials: List[Material] = []
    for row in rows[1:]:
        if index["pn"] >= len(row):
            continue
        pn = _to_text(row[index["pn"]])
        if not pn:
            continue
        materials.append(Material(
            pn=pn,
            name=_to_text(row[index["name"]]) if index["name"] < len(row) else "",
            spec=_to_text(row[index["spec"]]) if index["spec"] < len(row) else "",
            brand=_to_text(row[index["brand"]]) if index["brand"] < len(row) else "",
            stock=_to_float(row[index["stock"]]) if index["stock"] < len(row) else 0.0,
            disabled=_to_bool(row[index["disabled"]]) if index["disabled"] < len(row) else False,
            category=_to_text(row[index["category"]]) if index["category"] < len(row) else "",
        ))
    return materials


def _cache_path(cache_dir: Path, source: str) -> Path:
    digest = hashlib.md5(os.path.abspath(source).lower().encode("utf-8")).hexdigest()[:12]
    return cache_dir / f"materials-{digest}.json"


def load_materials(source: str, cache_dir: Optional[Path] = None,
                   use_cache: bool = True) -> List[Material]:
    """带缓存地加载物料表。

    缓存按「绝对路径 + 修改时间 + 大小」失效，避免每次都解析 1.5 万行 Excel。
    """
    source = os.path.abspath(source)
    if not os.path.isfile(source):
        raise FileNotFoundError(f"找不到物料表: {source}")

    stat = os.stat(source)
    meta = {"version": CACHE_VERSION, "path": source,
            "mtime": int(stat.st_mtime), "size": stat.st_size}

    cache_file = _cache_path(cache_dir, source) if (cache_dir and use_cache) else None
    if cache_file and cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if data.get("meta") == meta:
                return [Material.from_row(r) for r in data["rows"]]
        except Exception:
            pass

    materials = read_table(source)

    if cache_file:
        try:
            with open(cache_file, "w", encoding="utf-8") as fp:
                json.dump({"meta": meta, "rows": [m.to_row() for m in materials]},
                          fp, ensure_ascii=False)
        except Exception:
            pass

    return materials


# --------------------------------------------------------------------------
# 物料参数要求表（新建物料描述时参考）
# --------------------------------------------------------------------------
@dataclass
class RuleRow:
    name: str                 # 小类别/名称，如 "贴片电阻"
    order: str                # 参数要求及顺序
    example: str              # 示例


def read_rules(path: str) -> List[RuleRow]:
    """读取"物料参数要求表"，返回 名称/参数顺序/示例。"""
    if not os.path.isfile(path):
        return []
    ext = os.path.splitext(path)[1].lower()
    rows = _read_xls(path) if ext == ".xls" else _read_xlsx(path)
    result: List[RuleRow] = []
    for row in rows[2:]:  # 第 1 行大标题，第 2 行表头
        cells = [_to_text(v) for v in row]
        if len(cells) < 6:
            continue
        # 列：序号 | 大类别 | 小类别 | 名称 | 是否区分 | 参数要求及顺序 | 损耗 | 备注 | 示例
        name = cells[3]
        if not name:
            continue
        result.append(RuleRow(name=name, order=cells[5] if len(cells) > 5 else "",
                              example=cells[8] if len(cells) > 8 else ""))
    return result


def rule_example(rules: Sequence[RuleRow], keyword: str) -> str:
    """按名称关键字找示例描述。"""
    for rule in rules:
        if keyword in rule.name:
            return rule.example
    return ""
