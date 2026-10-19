"""KiCad 原理图 (.kicad_sch) 的最小 S 表达式读写工具。

设计目标
--------
1. 只读/改 `(symbol ...)` 顶层块的 `(property "名称" "值" ...)`；
2. **完全保留原文件排版**——所有修改都是"按字符区间替换"，不做整体重排；
3. 不依赖 kiutils 等第三方库，避免 KiCad 8/9/10 格式差异导致字段丢失。

注意事项
--------
* `.kicad_sch` 里有两类 `(symbol ...)`：`(lib_symbols ...)` 内部的符号定义，
  和真正放在图上的元件实例。本模块只处理 `lib_symbols` 之外的实例。
* 属性值只是"文本"，坐标/字体等参数原样保留。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

WHITESPACE = " \t\r\n"
MAX_DEPTH = 200


# --------------------------------------------------------------------------
# S 表达式节点
# --------------------------------------------------------------------------
class SNode:
    """S 表达式节点（list / atom / string）。只记录原始区间，不复制内容。"""

    __slots__ = ("kind", "start", "end", "children", "atom")

    def __init__(self, kind: str, start: int, end: int,
                 atom: Optional[str] = None,
                 children: Optional[List["SNode"]] = None):
        self.kind = kind          # "list" | "atom" | "str"
        self.start = start        # 起始偏移
        self.end = end            # 结束偏移（不含）
        self.atom = atom          # atom / str 的原始文本（str 带引号）
        self.children = children or []

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        if self.kind == "list":
            return f"SNode(list, {self.start}:{self.end}, {len(self.children)} children)"
        return f"SNode({self.kind}, {self.atom!r})"

    # -- 便捷访问 ---------------------------------------------------------
    @property
    def head(self) -> Optional[str]:
        """列表第一个原子（如 symbol / property / at）。"""
        if self.kind != "list" or not self.children:
            return None
        first = self.children[0]
        return first.atom if first.kind == "atom" else None

    def find(self, name: str) -> Optional["SNode"]:
        if self.kind != "list":
            return None
        for child in self.children[1:]:
            if child.kind == "list" and child.head == name:
                return child
        return None

    def find_all(self, name: str) -> List["SNode"]:
        if self.kind != "list":
            return []
        return [c for c in self.children[1:] if c.kind == "list" and c.head == name]


def _parse_string_end(text: str, i: int) -> int:
    """text[i] == '"'，返回闭合引号之后的下标。"""
    j = i + 1
    n = len(text)
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        j += 1
    raise ValueError("未闭合的字符串字面量")


def parse(text: str) -> SNode:
    """把整份文件解析成树。失败时抛 ValueError。"""
    n = len(text)

    def parse_node(i: int, depth: int) -> Tuple[SNode, int]:
        if depth > MAX_DEPTH:
            raise ValueError("S 表达式嵌套过深")
        while i < n and text[i] in WHITESPACE:
            i += 1
        if i >= n:
            raise ValueError("意外的文件结尾")
        c = text[i]
        if c == "(":
            start = i
            i += 1
            children: List[SNode] = []
            while True:
                while i < n and text[i] in WHITESPACE:
                    i += 1
                if i >= n:
                    raise ValueError(f"偏移 {start} 处的括号未闭合")
                if text[i] == ")":
                    return SNode("list", start, i + 1, children=children), i + 1
                node, i = parse_node(i, depth + 1)
                children.append(node)
        if c == '"':
            end = _parse_string_end(text, i)
            return SNode("str", i, end, atom=text[i:end]), end
        # 原子
        start = i
        while i < n and text[i] not in WHITESPACE and text[i] not in "()":
            i += 1
        if start == i:
            raise ValueError(f"偏移 {i} 处无法解析")
        return SNode("atom", start, i, atom=text[start:i]), i

    node, _ = parse_node(0, 0)
    return node


# --------------------------------------------------------------------------
# 字符串转义
# --------------------------------------------------------------------------
_ESCAPE_RE = re.compile(r"\\(.)", re.S)


def unquote(raw: str) -> str:
    """带引号的原始文本 -> 实际字符串。"""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return _ESCAPE_RE.sub(lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), raw)


def quote(value: str) -> str:
    """实际字符串 -> KiCad 文件里的带引号文本。"""
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + out + '"'


# --------------------------------------------------------------------------
# 元件（symbol 实例）
# --------------------------------------------------------------------------
#: 同一个字段可能出现的不同写法（区分大小写地按顺序尝试）
FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "PartNumber": ("PartNumber", "Partnumber", "Part Number", "PN", "P/N", "物料编码", "料号"),
    "Description": ("Description", "描述", "Desc"),
}


@dataclass
class Symbol:
    """一个原理图元件实例。"""

    node: SNode
    uuid: str = ""
    lib_id: str = ""
    fields: Dict[str, str] = field(default_factory=dict)
    #: 属性名（原始大小写） -> (property 节点, 值字符串节点)
    props: Dict[str, Tuple[SNode, SNode]] = field(default_factory=dict)
    prop_indent: str = "\t\t"

    # -- 便捷属性 ---------------------------------------------------------
    @property
    def reference(self) -> str:
        return self.fields.get("Reference", "")

    @property
    def value(self) -> str:
        return self.fields.get("Value", "")

    @property
    def footprint(self) -> str:
        return self.fields.get("Footprint", "")

    @property
    def description(self) -> str:
        return self.get_field("Description")

    @property
    def partnumber(self) -> str:
        return self.get_field("PartNumber")

    def get_field(self, canonical: str) -> str:
        for name in FIELD_ALIASES.get(canonical, (canonical,)):
            if name in self.fields:
                return self.fields[name]
        return ""

    def actual_field_name(self, canonical: str) -> Optional[str]:
        for name in FIELD_ALIASES.get(canonical, (canonical,)):
            if name in self.props:
                return name
        return None


def _line_indent(text: str, pos: int) -> str:
    """position 所在行的行首缩进。"""
    line_start = text.rfind("\n", 0, pos) + 1
    indent = text[line_start:pos]
    return indent if not indent.strip() else ""


def read_symbols(text: str, root: Optional[SNode] = None) -> List[Symbol]:
    """返回文件中所有元件实例（自动跳过 lib_symbols）。"""
    if root is None:
        root = parse(text)
    if root.kind != "list":
        return []

    symbols: List[Symbol] = []
    for child in root.children:
        if child.kind != "list" or child.head != "symbol":
            continue  # (lib_symbols ...) 里的 symbol 定义在下一层，不会命中

        sym = Symbol(node=child)
        first_prop: Optional[SNode] = None

        for sub in child.children[1:]:
            if sub.kind != "list":
                continue
            if sub.head == "lib_id":
                if len(sub.children) > 1 and sub.children[1].kind == "str":
                    sym.lib_id = unquote(sub.children[1].atom or "")
            elif sub.head == "uuid":
                if len(sub.children) > 1 and sub.children[1].kind == "str":
                    sym.uuid = unquote(sub.children[1].atom or "")
            elif sub.head == "property":
                if len(sub.children) < 3:
                    continue
                name_node, value_node = sub.children[1], sub.children[2]
                if name_node.kind != "str" or value_node.kind != "str":
                    continue
                name = unquote(name_node.atom or "")
                sym.fields[name] = unquote(value_node.atom or "")
                sym.props[name] = (sub, value_node)
                if first_prop is None:
                    first_prop = sub

        if first_prop is not None:
            indent = _line_indent(text, first_prop.start)
            if indent:
                sym.prop_indent = indent
        else:
            base = _line_indent(text, child.start)
            sym.prop_indent = (base or "") + "\t"

        symbols.append(sym)

    return symbols


# --------------------------------------------------------------------------
# 生成修改
# --------------------------------------------------------------------------
@dataclass
class Edit:
    """一处文本替换。"""

    start: int
    end: int
    text: str


def _build_property_block(name: str, value: str, indent: str) -> str:
    """按 KiCad 10 的排版生成一个隐藏属性块。"""
    i0 = indent
    i1 = indent + "\t"
    i2 = indent + "\t\t"
    i3 = indent + "\t\t\t"
    return (
        f'{i0}(property "{name}" {quote(value)}\n'
        f"{i1}(at 0 0 0)\n"
        f"{i1}(hide yes)\n"
        f"{i1}(effects\n"
        f"{i2}(font\n"
        f"{i3}(size 1.27 1.27)\n"
        f"{i2})\n"
        f"{i1})\n"
        f"{i0})"
    )


def plan_set_property(text: str, symbol: Symbol, canonical: str, value: str) -> Edit:
    """生成"设置某字段"的修改。

    已存在（含别名）则只替换值；不存在则在 symbol 结束前插入一个隐藏字段。
    """
    name = symbol.actual_field_name(canonical)
    if name is not None:
        _, value_node = symbol.props[name]
        return Edit(value_node.start, value_node.end, quote(value))

    insert_at = symbol.node.end - 1  # 指向 symbol 的右括号
    pos = insert_at
    while pos > symbol.node.start and text[pos - 1] in WHITESPACE:
        pos -= 1
    close_indent = text[pos:insert_at]
    block = _build_property_block(canonical, value, symbol.prop_indent)
    return Edit(pos, insert_at, "\n" + block + "\n" + close_indent)


def apply_edits(text: str, edits: Sequence[Edit]) -> str:
    """按区间倒序应用修改。"""
    out = text
    for edit in sorted(edits, key=lambda e: (e.start, e.end), reverse=True):
        out = out[:edit.start] + edit.text + out[edit.end:]
    return out


def sheet_files(text: str) -> List[str]:
    """返回原理图里引用到的子图文件名（相对路径，保持原样）。"""
    root = parse(text)
    result: List[str] = []
    for child in root.children:
        if child.kind != "list" or child.head != "sheet":
            continue
        for prop in child.find_all("property"):
            if len(prop.children) < 3:
                continue
            name_node, value_node = prop.children[1], prop.children[2]
            if name_node.kind != "str" or value_node.kind != "str":
                continue
            if unquote(name_node.atom or "") == "Sheetfile":
                name = unquote(value_node.atom or "")
                if name and name not in result:
                    result.append(name)
    return result


def load_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fp:
        return fp.read()


def write_file(path: str, text: str) -> None:
    """原子写入（先写临时文件再替换），避免中途失败损坏原理图。"""
    tmp = path + ".erp-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fp:
        fp.write(text)
    os.replace(tmp, path)


def backup_file(path: str) -> str:
    """生成一次性备份 `<name>.erp-bak`，返回备份路径。"""
    import shutil

    bak = path + ".erp-bak"
    shutil.copy2(path, bak)
    return bak


def collect_schematics(root_file: str, max_files: int = 64) -> List[str]:
    """从根原理图递归收集所有子图（绝对路径，去重）。"""
    root_file = os.path.abspath(root_file)
    seen: List[str] = []
    queue = [root_file]
    while queue and len(seen) < max_files:
        path = os.path.abspath(queue.pop(0))
        if path in seen or not os.path.isfile(path):
            continue
        seen.append(path)
        try:
            text = load_file(path)
            for name in sheet_files(text):
                sub = os.path.normpath(os.path.join(os.path.dirname(path), name))
                if sub not in seen:
                    queue.append(sub)
        except Exception:
            continue
    return seen
