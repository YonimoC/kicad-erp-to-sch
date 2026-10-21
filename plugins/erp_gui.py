"""ERP 物料匹配插件主界面。"""

from __future__ import annotations

import os
import re
import time
import traceback
import unicodedata
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import wx

import erp_rules
import kicad_session
import kicad_ui
import sch_sexpr
from erp_config import Config, PLUGIN_VERSION
from erp_data import Material, load_materials
from erp_match import (
    KIND_LABELS,
    Candidate,
    Component,
    ParsedMaterial,
    analyze_component,
    auto_select,
    build_candidates,
    build_generic_candidates,
    build_new_item,
    canonical_new_rule,
    is_electronic_pn,
    is_tht_footprint,
    material_fits,
    MaterialIndex,
    parse_material,
    parse_materials,
    search_keyword,
    search_normalize,
)

APP_TITLE = f"ERP 物料匹配（PartNumber / Description） v{PLUGIN_VERSION}"


def natural_key(text: str) -> str:
    """自然排序键：数字按数值大小排。"""
    return re.sub(r"(\d+)", lambda m: m.group(1).zfill(6), (text or "").lower())


@dataclass
class MatchRow:
    comp: Component
    status: str = "未匹配"
    candidates: List[Candidate] = field(default_factory=list)
    selected: Optional[Candidate] = None
    pn: str = ""
    description: str = ""
    note: str = ""
    #: 用户在界面里改过的 Value（写回原理图用）
    value_edit: str = ""
    #: 扫描时原理图里的原始 Value
    orig_value: str = ""
    #: 同一 Value + 封装组合下的所有元件（含多单元符号），写入时全部更新
    members: List[Component] = field(default_factory=list)

    @property
    def member_count(self) -> int:
        return max(1, len(self.members))

    @property
    def references(self) -> str:
        refs: List[str] = []
        for comp in (self.members or [self.comp]):
            if comp.reference not in refs:
                refs.append(comp.reference)
        if len(refs) <= 4:
            return ", ".join(refs)
        return f"{', '.join(refs[:3])} 等 {len(refs)} 个"

    @property
    def brand(self) -> str:
        return self.selected.brand if self.selected else ""

    @property
    def stock(self) -> str:
        if not self.selected:
            return ""
        stock = self.selected.material.stock
        return f"{stock:g}" if stock else "0"


# --------------------------------------------------------------------------
# 物料搜索对话框
# --------------------------------------------------------------------------
class MaterialSearchDialog(wx.Dialog):
    """在整个 ERP 物料表里搜索并选择物料。"""

    MAX_RESULTS = 1500

    def __init__(self, parent: wx.Window, materials: Sequence[Material],
                 keyword: str = "", title: str = "搜索 ERP 物料"):
        super().__init__(parent, title=title, size=(980, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.SetMinSize((780, 460))
        self.materials = materials
        self.results: List[Material] = []
        self.material: Optional[Material] = None
        self.sort_column = -1
        self.sort_ascending = True

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        top = wx.BoxSizer(wx.HORIZONTAL)
        top.Add(wx.StaticText(panel, label="关键字"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.txt_keyword = wx.TextCtrl(panel, value=keyword,
                                       style=wx.TE_PROCESS_ENTER)
        top.Add(self.txt_keyword, 1, wx.RIGHT, 6)
        self.btn_search = wx.Button(panel, label="搜索")
        top.Add(self.btn_search, 0, wx.RIGHT, 12)
        self.chk_disabled = wx.CheckBox(panel, label="包含禁用物料")
        top.Add(self.chk_disabled, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self.chk_electronic = wx.CheckBox(panel, label="仅电子物料（1 开头 9 位料号）")
        self.chk_electronic.SetValue(True)
        top.Add(self.chk_electronic, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(top, 0, wx.EXPAND | wx.ALL, 8)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for idx, (title_text, width) in enumerate([
            ("物料编码", 130), ("物料名称", 150), ("规格 / 描述", 430),
            ("品牌", 90), ("库存", 70), ("禁用", 50),
        ]):
            self.list.InsertColumn(idx, title_text, width=width)
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self.hint = wx.StaticText(panel, label="")
        sizer.Add(self.hint, 0, wx.ALL, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        self.btn_ok = wx.Button(panel, wx.ID_OK, "使用该物料")
        buttons.Add(self.btn_cancel, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_ok, 0)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)

        self.btn_search.Bind(wx.EVT_BUTTON, lambda e: self._search())
        self.txt_keyword.Bind(wx.EVT_TEXT_ENTER, lambda e: self._search())
        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_ok)
        self.chk_disabled.Bind(wx.EVT_CHECKBOX, lambda e: self._search())
        self.chk_electronic.Bind(wx.EVT_CHECKBOX, lambda e: self._search())
        self.list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)
        self._search()

    # -- 排序 -------------------------------------------------------------
    def _on_col_click(self, event) -> None:
        column = event.GetColumn()
        if column == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self._fill_list()

    def _sort_key(self, material: Material, column: int):
        if column == 0:
            return natural_key(material.pn)
        if column == 1:
            return material.name or ""
        if column == 2:
            return natural_key(material.spec)
        if column == 3:
            return material.brand or ""
        if column == 4:
            return float(material.stock or 0)
        if column == 5:
            return 1 if material.disabled else 0
        return ""

    def _fill_list(self) -> None:
        if self.sort_column >= 0:
            self.results.sort(key=lambda m: self._sort_key(m, self.sort_column),
                              reverse=not self.sort_ascending)
        self.list.DeleteAllItems()
        for idx, material in enumerate(self.results):
            self.list.InsertItem(idx, material.pn)
            self.list.SetItem(idx, 1, material.name)
            self.list.SetItem(idx, 2, material.spec)
            self.list.SetItem(idx, 3, material.brand)
            self.list.SetItem(idx, 4, f"{material.stock:g}" if material.stock else "0")
            self.list.SetItem(idx, 5, "是" if material.disabled else "")
            if material.disabled:
                self.list.SetItemTextColour(idx, wx.Colour(150, 150, 150))
        if self.results:
            self.list.Select(0)

        # 表头箭头
        titles = ["物料编码", "物料名称", "规格 / 描述", "品牌", "库存", "禁用"]
        for idx, title in enumerate(titles):
            text = title
            if idx == self.sort_column:
                text += " ▲" if self.sort_ascending else " ▼"
            item = wx.ListItem()
            item.SetText(text)
            item.SetWidth(self.list.GetColumnWidth(idx))
            self.list.SetColumn(idx, item)

    def _search(self) -> None:
        keyword = search_normalize(self.txt_keyword.GetValue().strip())
        include_disabled = self.chk_disabled.GetValue()
        only_electronic = self.chk_electronic.GetValue()
        keys = [k for k in keyword.split() if k]

        results: List[Material] = []
        for material in self.materials:
            if material.disabled and not include_disabled:
                continue
            if only_electronic and not is_electronic_pn(material.pn):
                continue
            haystack = search_normalize(
                f"{material.pn} {material.name} {material.spec} {material.brand}")
            if keys and not all(k in haystack for k in keys):
                continue
            results.append(material)
            if len(results) >= self.MAX_RESULTS:
                break

        exact = [m for m in results if keyword and search_normalize(m.pn) == keyword]
        if exact:
            results.remove(exact[0])
            results.insert(0, exact[0])

        self.results = results
        self._fill_list()

        text = f"共 {len(results)} 条"
        if len(results) >= self.MAX_RESULTS:
            text += f"（已截断到 {self.MAX_RESULTS} 条，请输入更精确的关键字）"
        self.hint.SetLabel(text)

    def _on_ok(self, _event) -> None:
        index = self.list.GetFirstSelected()
        if index < 0 or index >= len(self.results):
            wx.MessageBox("请先选择一个物料。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return
        self.material = self.results[index]
        self.EndModal(wx.ID_OK)


# --------------------------------------------------------------------------
# 候选物料对话框
# --------------------------------------------------------------------------
class CandidateDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, row: MatchRow,
                 materials: Sequence[Material]):
        super().__init__(parent, title=f"选择物料 - {row.comp.reference}", size=(980, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.SetMinSize((780, 440))
        self.row = row
        self.materials = materials
        self.choice: Optional[Candidate] = None
        self.manual: Optional[Material] = None
        self.manual_pn = ""
        self.manual_desc = ""

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        info = (f"位号 {row.references}    型号/数值 {row.comp.value}    "
                f"封装 {row.comp.footprint or '-'}")
        sizer.Add(wx.StaticText(panel, label=info), 0, wx.ALL, 8)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for idx, (title, width) in enumerate([
            ("物料编码", 130), ("描述", 500), ("品牌", 90), ("库存", 80), ("优先级", 80),
        ]):
            self.list.InsertColumn(idx, title, width=width)
        # 默认按自动匹配的优先级排序；点表头可改排序
        self.candidates: List[Candidate] = list(row.candidates)
        self.sort_column = -1
        self.sort_ascending = True
        self._fill_list()
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_search = wx.Button(panel, label="搜索全部物料…")
        self.btn_manual = wx.Button(panel, label="手动输入…")
        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        self.btn_ok = wx.Button(panel, wx.ID_OK, "使用选中物料")
        buttons.Add(self.btn_search, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_manual, 0, wx.RIGHT, 8)
        buttons.AddStretchSpacer()
        buttons.Add(self.btn_cancel, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_ok, 0)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)

        self.btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        self.btn_manual.Bind(wx.EVT_BUTTON, self._on_manual)
        self.btn_search.Bind(wx.EVT_BUTTON, self._on_search)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_ok)
        self.list.Bind(wx.EVT_LIST_COL_CLICK, self._on_col_click)

    # -- 排序 -------------------------------------------------------------
    #: 列表表头（排序箭头要重写表头文字）
    TITLES = ["物料编码", "描述", "品牌", "库存", "优先级"]

    def _priority_text(self, candidate: Candidate) -> str:
        return ("优选" if candidate.brand_rank < 100 else
                ("备选" if candidate.brand_rank < 1000 else "其他"))

    def _on_col_click(self, event) -> None:
        column = event.GetColumn()
        if column == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self._fill_list()

    def _sort_key(self, candidate: Candidate, column: int):
        if column == 0:
            return natural_key(candidate.pn)
        if column == 1:
            return natural_key(candidate.description)
        if column == 2:
            return candidate.brand or ""
        if column == 3:
            return float(candidate.material.stock or 0)
        if column == 4:
            return candidate.brand_rank
        return ""

    def _fill_list(self) -> None:
        selected = self.list.GetFirstSelected()
        keep_pn = ""
        if 0 <= selected < len(self.candidates):
            keep_pn = self.candidates[selected].pn
        if self.sort_column >= 0:
            self.candidates.sort(key=lambda c: self._sort_key(c, self.sort_column),
                                 reverse=not self.sort_ascending)
        self.list.DeleteAllItems()
        for i, candidate in enumerate(self.candidates):
            self.list.InsertItem(i, candidate.pn)
            self.list.SetItem(i, 1, candidate.description)
            self.list.SetItem(i, 2, candidate.brand)
            self.list.SetItem(i, 3, f"{candidate.material.stock:g}")
            self.list.SetItem(i, 4, self._priority_text(candidate))
            if candidate.material.disabled:
                self.list.SetItemTextColour(i, wx.Colour(150, 150, 150))
        if self.candidates:
            index = 0
            for i, candidate in enumerate(self.candidates):
                if keep_pn and candidate.pn == keep_pn:
                    index = i
                    break
            self.list.Select(index)
            self.list.EnsureVisible(index)
        # 表头排序箭头
        for idx, title in enumerate(self.TITLES):
            text = title
            if idx == self.sort_column:
                text += " ▲" if self.sort_ascending else " ▼"
            item = wx.ListItem()
            item.SetText(text)
            item.SetWidth(self.list.GetColumnWidth(idx))
            self.list.SetColumn(idx, item)

    def _on_ok(self, _event) -> None:
        index = self.list.GetFirstSelected()
        if index < 0 or index >= len(self.candidates):
            wx.MessageBox("请先选择一个物料。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return
        self.choice = self.candidates[index]
        self.EndModal(wx.ID_OK)

    def _on_search(self, _event) -> None:
        keyword = search_keyword(self.row.comp)
        with MaterialSearchDialog(self, self.materials, keyword) as dlg:
            if dlg.ShowModal() != wx.ID_OK or dlg.material is None:
                return
            self.manual = dlg.material
            self.EndModal(wx.ID_OK)

    def _on_manual(self, _event) -> None:
        with wx.TextEntryDialog(self, "物料编码（PartNumber）", "手动输入") as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            pn = dlg.GetValue().strip()
        with wx.TextEntryDialog(self, "描述（Description）", "手动输入") as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            desc = dlg.GetValue().strip()
        if not pn:
            return
        self.manual_pn = pn
        self.manual_desc = desc
        self.EndModal(wx.ID_OK)


# --------------------------------------------------------------------------
# 参数要求表（内置）
# --------------------------------------------------------------------------
class RulesDialog(wx.Dialog):
    def __init__(self, parent: wx.Window):
        super().__init__(parent, title="物料参数要求（内置）", size=(760, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.SetMinSize((620, 420))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        text = wx.TextCtrl(panel, value=erp_rules.rule_text(),
                           style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
        font = text.GetFont()
        font.SetFamily(wx.FONTFAMILY_TELETYPE)
        text.SetFont(font)
        sizer.Add(text, 1, wx.EXPAND | wx.ALL, 8)
        btn = wx.Button(panel, wx.ID_OK, "关闭")
        sizer.Add(btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)
        btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))


# --------------------------------------------------------------------------
# 编辑元件对话框（双击）
# --------------------------------------------------------------------------
class EditRowDialog(wx.Dialog):
    """双击行后的编辑：Value / PartNumber / 描述 均可改。"""

    def __init__(self, parent: wx.Window, row: MatchRow):
        super().__init__(parent, title=f"编辑元件 - {row.references}", size=(720, 380),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.SetMinSize((620, 340))
        self.row = row
        comp = row.comp

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        info = (f"位号 {row.references}    类别 {comp.kind_label}    "
                f"封装 {comp.footprint or '-'}    状态 {row.status}")
        sizer.Add(wx.StaticText(panel, label=info), 0, wx.ALL, 10)

        grid = wx.FlexGridSizer(3, 2, 8, 8)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(panel, label="Value / 型号"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_value = wx.TextCtrl(panel, value=comp.value)
        grid.Add(self.txt_value, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="PartNumber"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_pn = wx.TextCtrl(panel, value=row.pn)
        grid.Add(self.txt_pn, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="描述"), 0, wx.ALIGN_TOP)
        self.txt_desc = wx.TextCtrl(panel, value=row.description,
                                    style=wx.TE_MULTILINE, size=(-1, 110))
        grid.Add(self.txt_desc, 1, wx.EXPAND)
        sizer.Add(grid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        hint = wx.StaticText(panel, label="修改 Value 会在写入原理图时一并写回 Value 字段。")
        sizer.Add(hint, 0, wx.ALL, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        self.btn_ok = wx.Button(panel, wx.ID_OK, "使用")
        buttons.Add(self.btn_cancel, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_ok, 0)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)
        self.txt_value.SetFocus()
        self.txt_value.SelectAll()

    def values(self) -> Tuple[str, str, str]:
        return (self.txt_value.GetValue().strip(),
                self.txt_pn.GetValue().strip(),
                self.txt_desc.GetValue().strip())


# --------------------------------------------------------------------------
# 新建物料对话框（可选类别）
# --------------------------------------------------------------------------
class NewItemDialog(wx.Dialog):
    """按内置参数要求表生成新料描述，类别可选、描述可编辑。"""

    def __init__(self, parent: wx.Window, row: MatchRow, config: Config):
        super().__init__(parent, title=f"生成新料描述 - {row.references}", size=(860, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.SetMinSize((720, 400))
        self.row = row
        self.comp = row.comp
        self.config = config

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        info = (f"位号 {row.references}    型号/数值 {self.comp.value}    "
                f"封装 {self.comp.footprint or '-'}")
        sizer.Add(wx.StaticText(panel, label=info), 0, wx.ALL, 10)

        grid = wx.FlexGridSizer(2, 2, 8, 8)
        grid.AddGrowableCol(1, 1)
        grid.Add(wx.StaticText(panel, label="物料类别"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.names = erp_rules.rule_names()
        self.choice = wx.Choice(panel, choices=self.names)
        suggested = erp_rules.suggest_rule_name(self.comp)
        if suggested in self.names:
            self.choice.SetSelection(self.names.index(suggested))
        elif self.names:
            self.choice.SetSelection(0)
        grid.Add(self.choice, 1, wx.EXPAND)

        grid.Add(wx.StaticText(panel, label="PartNumber"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_pn = wx.TextCtrl(panel, value=str(config.get("new_partnumber") or "新料"))
        grid.Add(self.txt_pn, 1, wx.EXPAND)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.hint = wx.StaticText(panel, label="")
        sizer.Add(self.hint, 0, wx.EXPAND | wx.ALL, 10)

        sizer.Add(wx.StaticText(panel, label="描述（可直接修改）"), 0, wx.LEFT, 10)
        self.txt_desc = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 110))
        sizer.Add(self.txt_desc, 1, wx.EXPAND | wx.ALL, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        self.btn_ok = wx.Button(panel, wx.ID_OK, "使用")
        buttons.Add(self.btn_cancel, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_ok, 0)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)

        self.choice.Bind(wx.EVT_CHOICE, lambda e: self._regenerate())
        self._regenerate()

    def _regenerate(self) -> None:
        name = self.choice.GetStringSelection()
        rule = erp_rules.find_rule(name)
        if rule:
            self.hint.SetLabel(f"参数顺序：{rule.get('order', '')}\n示例：{rule.get('example', '')}")
        self.hint.Wrap(760)
        # 与自动新料保持完全一致（如“IC类”→“贴片集成电路”）
        display = ""
        try:
            rule_name, disp = canonical_new_rule(self.comp)
            if rule_name and rule_name == name:
                display = disp
        except Exception:
            display = ""
        self.txt_desc.SetValue(
            erp_rules.build_description(name, self.comp, self.config, display_name=display))

    def values(self) -> Tuple[str, str]:
        return self.txt_pn.GetValue().strip(), self.txt_desc.GetValue().strip()


# --------------------------------------------------------------------------
# 可勾选的下拉面板（勾选后不消失，点外面才关闭）
# --------------------------------------------------------------------------
class CheckPopup(wx.PopupTransientWindow):
    """弹出式多选面板：标题 + 复选框网格 + 底部小按钮。

    用 PopupTransientWindow 而不是 Menu，勾选/取消勾选后**不会关闭**，
    可以连续改多项；点面板外面或按 Esc 才消失。
    """

    BG = wx.Colour(252, 252, 252)
    FRAME = wx.Colour(196, 200, 206)
    TITLE = wx.Colour(72, 76, 82)
    TEXT = wx.Colour(48, 52, 58)
    LINK = wx.Colour(28, 100, 178)

    def __init__(self, parent: wx.Window, title: str,
                 items: Sequence[Tuple[str, str, bool]],
                 on_toggle, on_all=None, columns: int = 2):
        # 外层留 1px 灰边（用背景色模拟，比 BORDER_SIMPLE 的 3D 边框干净）
        super().__init__(parent, wx.BORDER_NONE)
        self.SetBackgroundColour(self.FRAME)
        self.on_toggle = on_toggle
        self.checks: Dict[str, wx.CheckBox] = {}

        body = wx.Panel(self)
        body.SetBackgroundColour(self.BG)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 标题
        label = wx.StaticText(body, label=title)
        font = label.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        label.SetFont(font)
        label.SetForegroundColour(self.TITLE)
        sizer.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 14)

        # 分隔线
        sizer.Add(wx.StaticLine(body), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 14)

        # 复选框（多列对齐）
        grid = wx.FlexGridSizer(0, max(1, columns), 9, 26)
        for key, text, checked in items:
            check = wx.CheckBox(body, label=text)
            check.SetValue(bool(checked))
            check.SetForegroundColour(self.TEXT)
            check.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            check.Bind(wx.EVT_CHECKBOX,
                       lambda event, k=key: self._on_check(k, event.IsChecked()))
            grid.Add(check, 0, wx.ALIGN_CENTER_VERTICAL)
            self.checks[key] = check
        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 14)

        # 底部操作（三个按钮水平均分）
        sizer.Add(wx.StaticLine(body), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 14)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self._small_button(body, "全选", lambda: self._set_all(True, on_all)),
                    1, wx.EXPAND | wx.RIGHT, 8)
        buttons.Add(self._small_button(body, "全不选", lambda: self._set_all(False, on_all)),
                    1, wx.EXPAND | wx.RIGHT, 8)
        buttons.Add(self._small_button(body, "关闭", self.Dismiss), 1, wx.EXPAND)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)

        body.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(body, 1, wx.EXPAND | wx.ALL, 1)
        self.SetSizer(outer)
        outer.Fit(self)
        width = max(260, self.GetSize().width)
        self.SetSize((width, self.GetSize().height))
        self.SetMinSize((width, self.GetSize().height))

    def _small_button(self, parent: wx.Window, text: str, handler) -> wx.Window:
        """底部操作按钮（标准按钮，尺寸紧凑）。"""
        button = wx.Button(parent, label=text, style=wx.BU_EXACTFIT)
        button.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        button.Bind(wx.EVT_BUTTON, lambda e: handler())
        return button

    def show_below(self, anchor: wx.Window, gap: int = 4,
                   align_right: bool = False) -> None:
        """贴在控件下方弹出（align_right=True 时右边缘对齐），不超出屏幕。"""
        size = self.GetSize()
        offset_x = anchor.GetSize().width - size.width if align_right else 0
        pos = anchor.ClientToScreen(wx.Point(offset_x, anchor.GetSize().height + gap))
        index = wx.Display.GetFromPoint(pos)
        if index != wx.NOT_FOUND:
            area = wx.Display(index).GetClientArea()
            x = min(max(pos.x, area.x + 4), area.x + area.width - size.width - 4)
            y = min(max(pos.y, area.y + 4), area.y + area.height - size.height - 4)
            pos = wx.Point(x, y)
        self.Position(pos, (0, 0))
        self.Popup()

    def _on_check(self, key: str, checked: bool) -> None:
        try:
            self.on_toggle(key, checked)
        except Exception:
            pass

    def _set_all(self, value: bool, on_all) -> None:
        for key, check in self.checks.items():
            check.SetValue(value)
            self._on_check(key, value)
        if on_all:
            try:
                on_all(value)
            except Exception:
                pass
        self.Layout()



# --------------------------------------------------------------------------
# 分割按钮：左边主操作，右边 ▾ 小格下拉
# 用两个原生 wx.Button 紧贴拼成，字体/圆角/边框/悬停效果与同界面其它按钮一致
# --------------------------------------------------------------------------
class SplitButton(wx.Panel):
    """一个按钮两种点击：主体按钮执行主操作，右侧 ▾ 小按钮弹选择面板。"""

    ARROW = 26
    HEIGHT = 30

    def __init__(self, parent: wx.Window, label: str, on_click, on_menu):
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self._label = label
        self._on_click = on_click
        self._on_menu = on_menu
        self.SetBackgroundColour(parent.GetBackgroundColour())

        self.btn_main = wx.Button(self, label=label)
        self.btn_arrow = wx.Button(self, label="▼")
        try:  # 箭头小一点，避免看着笨重
            self.btn_arrow.SetFont(parent.GetFont().Scaled(0.75))
        except Exception:  # noqa: BLE001
            pass
        for button in (self.btn_main, self.btn_arrow):
            button.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            button.SetMinSize((-1, self.HEIGHT))
        self.btn_arrow.SetMinSize((self.ARROW, self.HEIGHT))

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.btn_main, 1, wx.EXPAND)
        sizer.Add(self.btn_arrow, 0, wx.EXPAND)
        self.SetSizer(sizer)
        self._fit_size()

        self.btn_main.Bind(wx.EVT_BUTTON, lambda e: self._fire(self._on_click))
        self.btn_arrow.Bind(wx.EVT_BUTTON, lambda e: self._fire(self._on_menu))
        self.btn_main.Bind(wx.EVT_KEY_DOWN, self._on_main_key)

    # -- 外观 -------------------------------------------------------------
    def _fit_size(self) -> None:
        best = self.btn_main.GetBestSize()
        height = max(self.HEIGHT, best.height)
        size = (best.width + self.ARROW, height)
        self.SetMinSize(size)
        self.SetInitialSize(size)
        for button in (self.btn_main, self.btn_arrow):
            button.SetMinSize((-1, height))
        self.btn_arrow.SetMinSize((self.ARROW, height))
        self.Layout()

    def SetLabel(self, label: str) -> None:  # noqa: N802 (wx 风格)
        if label == self._label:
            return
        self._label = label
        self.btn_main.SetLabel(label)
        self._fit_size()
        parent = self.GetParent()
        if parent:
            parent.Layout()

    def GetLabel(self) -> str:  # noqa: N802 (wx 风格)
        return self._label

    def Enable(self, enable: bool = True) -> bool:  # noqa: N802 (wx 风格)
        ok = super().Enable(enable)
        self.btn_main.Enable(enable)
        self.btn_arrow.Enable(enable)
        return ok

    def EnableAction(self, enable: bool) -> None:  # noqa: N802 (wx 风格)
        """只启用/禁用主按钮；右侧 ▾ 始终可点，方便重新打开选择面板。"""
        self.btn_main.Enable(enable)

    def SetToolTip(self, *args, **kwargs) -> None:  # noqa: N802 (wx 风格)
        super().SetToolTip(*args, **kwargs)
        for button in (self.btn_main, self.btn_arrow):
            button.SetToolTip(*args, **kwargs)

    # -- 交互 -------------------------------------------------------------
    def _fire(self, handler) -> None:
        if self.IsEnabled() and handler:
            handler()

    def _on_main_key(self, event) -> None:
        key = event.GetKeyCode()
        if key == wx.WXK_DOWN and self._on_menu:
            self._on_menu()
        else:
            event.Skip()


# --------------------------------------------------------------------------
# 主对话框
# --------------------------------------------------------------------------
class ErpToSchDialog(wx.Dialog):
    #: 窗口最小尺寸（低于此不能再缩小）
    MIN_SIZE = (1000, 620)

    def __init__(self, parent: Optional[wx.Window] = None):
        super().__init__(parent, title=APP_TITLE,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
                         | wx.MAXIMIZE_BOX | wx.MINIMIZE_BOX)
        self.config = Config()
        self.materials: List[Material] = []
        self.parsed_materials: List[ParsedMaterial] = []
        #: 料号 -> 解析后的物料（搜索后回显库存/品牌用）
        self._parsed_by_pn: Dict[str, ParsedMaterial] = {}
        #: 物料索引（按类别/贴片插件/数值分桶，加速匹配）
        self.matindex: Optional[MaterialIndex] = None
        self.rows: List[MatchRow] = []
        self.schematic_files: List[str] = []
        self.root_schematic = ""
        self.sort_column = -1
        self.sort_ascending = True
        self._column_titles: List[str] = []
        self._column_widths: List[int] = []
        #: 上一次实际应用的列宽（避免重复设列宽导致重排死循环）
        self._col_widths_applied: List[int] = []
        #: 写入范围（默认全选）
        self.write_filters: Dict[str, bool] = {"已匹配": True, "新料": True, "手动": True}
        #: 参与自动匹配的类别（默认全选）
        self.match_kinds: Dict[str, bool] = {}
        #: 工程目录是否由用户手动选过（手动时优先用手动值）
        self._project_manual = False
        #: 双击单元格就地编辑：当前编辑框与其上下文（行、字段）
        self._editor: Optional[wx.TextCtrl] = None
        self._editor_ctx: Optional[Tuple[MatchRow, str]] = None
        #: 上次编辑的列（键盘回车激活时沿用）
        self._last_edit_column = 2
        #: 退出全屏/最大化时恢复的窗口尺寸
        self._restore_size: Optional[Tuple[int, int]] = None

        self._build_ui()
        self._detect_project()
        # 先把窗口显示出来，物料表读取 / 解析 / 自动扫描放到空闲时做，避免开窗卡顿
        wx.CallAfter(self._startup)

    # -- UI ---------------------------------------------------------------
    @staticmethod
    def _tool_button(panel: wx.Window, label: str) -> wx.Button:
        """普通按钮：高度与分割按钮保持一致，让工具栏看起来齐平。"""
        button = wx.Button(panel, label=label)
        button.SetMinSize((-1, SplitButton.HEIGHT))
        return button

    # -- 窗口自适应 -------------------------------------------------------
    #: 各列最小宽度（窗口再窄就出现横向滚动条，不继续压）
    _COL_MIN = [90, 46, 90, 66, 86, 100, 150, 54, 44, 40]
    #: 自动吃掉剩余宽度的列（描述）
    _FLEX_COL = 6

    def _apply_window_bounds(self) -> None:
        """按屏幕工作区自适应初始尺寸，并设定最小尺寸（低于此不能再缩）。"""
        area = wx.Display().GetClientArea()
        index = wx.Display.GetFromWindow(self)
        if index != wx.NOT_FOUND:
            area = wx.Display(index).GetClientArea()
        width = max(self.MIN_SIZE[0], int(area.width * 0.92))
        height = max(self.MIN_SIZE[1], int(area.height * 0.92))
        self.SetMinSize(self.MIN_SIZE)
        # 上次的窗口大小（在屏幕内、不小于最小尺寸时沿用）
        saved = self.config.get("window_size") or []
        if (isinstance(saved, (list, tuple)) and len(saved) == 2
                and self.MIN_SIZE[0] <= int(saved[0]) <= area.width
                and self.MIN_SIZE[1] <= int(saved[1]) <= area.height):
            width, height = int(saved[0]), int(saved[1])
        self.SetSize((min(width, area.width), min(height, area.height)))
        self.Centre()

    def _on_window_size(self, event) -> None:
        """窗口缩放：重排列宽（描述列吃剩余空间），并取消正在编辑的单元格。"""
        event.Skip()
        if getattr(self, "_editor", None) is not None:
            self._cancel_inline_edit()
        wx.CallAfter(self._layout_columns)

    def _on_char_hook(self, event) -> None:
        """F11 切换全屏（全屏时表格能充分利用屏幕）。"""
        if event.GetKeyCode() == wx.WXK_F11:
            self._toggle_fullscreen()
        else:
            event.Skip()

    def _toggle_fullscreen(self) -> None:
        try:
            if self.IsFullScreen():
                self.ShowFullScreen(False)
                if self._restore_size:
                    self.SetSize(self._restore_size)
            else:
                self._restore_size = (self.GetSize().width, self.GetSize().height)
                self.ShowFullScreen(True, wx.FULLSCREEN_ALL)
        except Exception:  # noqa: BLE001  某些窗口管理器不支持全屏时退回最大化
            self.Maximize(not self.IsMaximized())
        wx.CallAfter(self._layout_columns)

    def _layout_columns(self) -> None:
        """按当前列表可用宽度重新分配列宽。"""
        total = self.list.GetClientSize().width
        if total <= 0 or not self._column_widths:
            return
        flex = self._FLEX_COL
        widths = list(self._column_widths)
        others = [i for i in range(len(widths)) if i != flex]
        others_total = sum(widths[i] for i in others)
        rest = total - others_total - 4       # 4px 余量，免得老是弹横向滚动条
        if rest >= self._COL_MIN[flex]:
            widths[flex] = rest
        else:
            # 空间不够：非描述列按各自可压缩空间等比让出，保证描述列不低于最小宽
            deficit = self._COL_MIN[flex] - rest
            room = {i: max(0, widths[i] - self._COL_MIN[i]) for i in others}
            total_room = sum(room.values())
            if total_room > 0:
                for idx, space in room.items():
                    if not space:
                        continue
                    widths[idx] -= min(space, int(round(deficit * space / total_room)))
            widths[flex] = max(self._COL_MIN[flex],
                               total - sum(widths[i] for i in others) - 4)
        if widths == self._col_widths_applied:
            return
        self._col_widths_applied = widths
        for idx, width in enumerate(widths):
            if self.list.GetColumnWidth(idx) != width:
                self.list.SetColumnWidth(idx, width)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(2, 3, 6, 8)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(panel, label="ERP 物料表"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_erp = wx.TextCtrl(panel, value=self.config.erp_file)
        grid.Add(self.txt_erp, 1, wx.EXPAND)
        btn_erp = self._tool_button(panel, "浏览…")
        grid.Add(btn_erp, 0, wx.ALIGN_CENTER_VERTICAL)

        grid.Add(wx.StaticText(panel, label="工程目录"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_project = wx.TextCtrl(panel)
        grid.Add(self.txt_project, 1, wx.EXPAND)
        btn_proj = self._tool_button(panel, "浏览…")
        grid.Add(btn_proj, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(grid, 0, wx.EXPAND | wx.ALL, 10)

        tools = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reload = self._tool_button(panel, "重新加载物料表")
        self.btn_scan = self._tool_button(panel, "扫描")
        self.btn_match = SplitButton(panel, "匹配",
                                     on_click=lambda: self._match_all(),
                                     on_menu=self._on_match_menu)
        self.btn_pick = self._tool_button(panel, "选择候选…")
        self.btn_search = self._tool_button(panel, "搜索物料…")
        self.btn_rules = self._tool_button(panel, "参数要求…")
        for button in (self.btn_reload, self.btn_scan, self.btn_match,
                       self.btn_pick, self.btn_search, self.btn_rules):
            tools.Add(button, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        tools.AddStretchSpacer()
        self.btn_apply = SplitButton(panel, "写入原理图",
                                     on_click=lambda: self._apply(),
                                     on_menu=self._on_write_menu)
        tools.Add(self.btn_apply, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(tools, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self._column_titles = ["位号", "类别", "Value/型号", "封装", "状态",
                               "PartNumber", "描述", "品牌", "库存", "候选"]
        self._column_widths = [150, 60, 150, 90, 105, 125, 340, 70, 60, 50]
        for idx, (title, width) in enumerate(zip(self._column_titles, self._column_widths)):
            self.list.InsertColumn(idx, title, width=width)
        root.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.status = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 96))
        root.Add(self.status, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(root)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)
        self._apply_window_bounds()
        self.Layout()

        self.txt_erp.Bind(wx.EVT_KILL_FOCUS, lambda e: self._on_path_changed())
        btn_erp.Bind(wx.EVT_BUTTON, lambda e: self._browse_erp())
        btn_proj.Bind(wx.EVT_BUTTON, lambda e: self._browse_project())
        self.btn_reload.Bind(wx.EVT_BUTTON, lambda e: self._load_data(verbose=True, force=True))
        self.btn_scan.Bind(wx.EVT_BUTTON, lambda e: self._scan())
        self.btn_pick.Bind(wx.EVT_BUTTON, lambda e: self._pick_candidate())
        self.btn_search.Bind(wx.EVT_BUTTON, lambda e: self._search_material())
        self.btn_rules.Bind(wx.EVT_BUTTON, lambda e: self._show_rules())
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._activate_row)
        self.list.Bind(wx.EVT_LIST_COL_CLICK, self._on_column_click)
        self.list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_right_click)
        self.list.Bind(wx.EVT_SIZE, lambda e: (e.Skip(), wx.CallAfter(self._layout_columns)))
        self.Bind(wx.EVT_SIZE, self._on_window_size)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        wx.CallAfter(self._layout_columns)

    # -- 路径 -------------------------------------------------------------
    def _browse_erp(self) -> None:
        with wx.FileDialog(self, "选择 ERP 物料表",
                           wildcard="Excel 文件 (*.xls;*.xlsx)|*.xls;*.xlsx",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.txt_erp.SetValue(dlg.GetPath())
                self._on_path_changed()
                self._load_data(verbose=True, force=True)

    def _browse_project(self) -> None:
        with wx.DirDialog(self, "选择 KiCad 工程目录",
                          defaultPath=self.txt_project.GetValue() or "") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.txt_project.SetValue(dlg.GetPath())
                self._project_manual = True
                self._on_path_changed()

    def _on_path_changed(self) -> None:
        self.config.set("erp_file", self.txt_erp.GetValue().strip())
        self.config.save()

    def _detect_project(self) -> None:
        """每次都重新从 KiCad 取当前工程（不保存、不沿用上次）。"""
        filename, project_dir = kicad_session.get_open_schematic()
        detected = ""
        if project_dir and os.path.isdir(project_dir):
            detected = os.path.abspath(project_dir)
        elif filename:
            path = kicad_session.find_schematic(filename)
            if path:
                detected = os.path.dirname(path)
        if not detected:
            for folder in (kicad_session.process_hint_dirs()
                           + kicad_session.open_project_dirs()):
                root = kicad_session.find_root_schematic(folder)
                if root:
                    detected = os.path.dirname(root)
                    break

        if detected:
            self.txt_project.SetValue(detected)
            self._log(f"工程目录: {detected}")
        else:
            self.txt_project.SetValue("")
            self._log("未检测到当前打开的工程（请确认已在 KiCad 中打开原理图）。")

    # -- 启动 -------------------------------------------------------------
    def _startup(self) -> None:
        """窗口出现后再读物料表并自动扫描（只读，不改任何东西）。"""
        self._log("正在读取 ERP 物料表…")
        self._load_data(verbose=True)
        self._auto_scan()

    def _parsed_cache_file(self) -> Optional[Path]:
        """物料解析缓存文件（ERP 文件变了就换一个，自动失效）。"""
        erp = self.txt_erp.GetValue().strip()
        cache_dir = self.config.cache_dir
        if not erp or not cache_dir:
            return None
        try:
            stat = os.stat(erp)
        except OSError:
            return None
        key = f"{erp}|{int(stat.st_mtime)}|{stat.st_size}|{PLUGIN_VERSION}"
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        return Path(cache_dir) / f"parsed-{digest}.json"

    @staticmethod
    def _prune_parse_cache(keep: Optional[Path]) -> None:
        """删掉 ERP 历史版本遗留的解析缓存，只留当前这份。"""
        if keep is None:
            return
        try:
            for old in Path(keep).parent.glob("parsed-*.json"):
                if old.name != Path(keep).name:
                    old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001  清理失败不影响使用
            pass

    def _load_data(self, verbose: bool = True, force: bool = False) -> None:
        erp = self.txt_erp.GetValue().strip()
        cursor = wx.BusyCursor()
        started = time.time()
        try:
            if force or not self.materials:
                self.materials = load_materials(erp, self.config.cache_dir)
                cache_file = self._parsed_cache_file()
                self.parsed_materials = parse_materials(
                    self.materials, self.config, cache_file=cache_file)
                self._parsed_by_pn = {pm.material.pn: pm for pm in self.parsed_materials}
                self.matindex = MaterialIndex(self.parsed_materials)
                self._prune_parse_cache(cache_file)
        except Exception as exc:  # noqa: BLE001
            del cursor
            self._log(f"物料表读取失败: {exc}")
            wx.MessageBox(f"物料表读取失败:\n{exc}", APP_TITLE, wx.OK | wx.ICON_WARNING, self)
            return
        del cursor
        if verbose:
            elapsed = (time.time() - started) * 1000
            self._log(f"物料表: {len(self.materials)} 条（解析 {len(self.parsed_materials)} 条，"
                      f"耗时 {elapsed:.0f} ms）")

    # -- 扫描 -------------------------------------------------------------
    def _resolve_root_schematic(self) -> str:
        filename, ipc_dir = kicad_session.get_open_schematic()
        project = self.txt_project.GetValue().strip()

        def try_dir(folder: str) -> str:
            if not folder or not os.path.isdir(folder):
                return ""
            # 先按当前打开的原理图文件名找（同一工程内换图纸也能对上）
            for name in ([filename] if filename else []):
                candidate = os.path.join(folder, name)
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate)
            return kicad_session.find_root_schematic(folder) or ""

        # 用户手动选过工程目录 → 先按手动选择来
        if self._project_manual and project:
            found = try_dir(project)
            if found:
                return found

        # 否则以 KiCad 当前打开的工程为准，再退回界面里的目录
        for folder in (ipc_dir, project):
            found = try_dir(folder)
            if found:
                return found

        for folder in (kicad_session.process_hint_dirs()
                       + kicad_session.open_project_dirs()):
            found = try_dir(folder)
            if found:
                return found
        return ""

    def _auto_scan(self) -> None:
        """打开插件后自动扫描一次（只读，不会覆盖已修改的数据）。"""
        try:
            self._scan(interactive=False, verbose=False)
        except Exception:  # noqa: BLE001
            self._log(traceback.format_exc())

    def _row_from_schematic(self, comp: Component) -> MatchRow:
        """扫描时生成一行：只反映原理图当前内容，不做任何匹配。"""
        row = MatchRow(comp=comp, orig_value=comp.value)
        if comp.do_not_place:
            row.status = "不上件跳过"
            row.note = "Value 为 NC/DNP，不上件"
            return row
        if comp.partnumber or comp.description:
            row.pn = comp.partnumber
            row.description = comp.description
            row.status = "已是最新"
            row.note = "原理图里已有 PartNumber/描述"
            return row
        row.status = "未匹配"
        row.note = "点「匹配」按 ERP 物料表自动匹配"
        return row

    def _ensure_materials(self) -> None:
        """物料表还没读（启动尚未完成）时先补上，避免扫到空表。"""
        if not self.materials:
            self._load_data(verbose=False)

    def _scan(self, interactive: bool = True, verbose: bool = True) -> None:
        self._ensure_materials()
        root = self._resolve_root_schematic()
        if not root:
            if not interactive:
                self._log("未自动找到原理图，请点「扫描」手动选择。")
                return
            with wx.FileDialog(self, "未自动找到原理图，请选择工程的根原理图",
                               wildcard="KiCad 原理图 (*.kicad_sch)|*.kicad_sch",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                root = dlg.GetPath()

        self.root_schematic = root
        project_dir = os.path.dirname(root)
        if self.txt_project.GetValue().strip() != project_dir:
            self.txt_project.SetValue(project_dir)
            self._on_path_changed()

        cursor = wx.BusyCursor()
        try:
            self.schematic_files = sch_sexpr.collect_schematics(root)
            self.rows = []
            skipped = 0
            for path in self.schematic_files:
                text = sch_sexpr.load_file(path)
                for symbol in sch_sexpr.read_symbols(text):
                    ref = symbol.reference
                    if not ref or ref.startswith("#"):
                        skipped += 1
                        continue
                    comp = Component(
                        file=path,
                        uuid=symbol.uuid,
                        reference=ref,
                        value=symbol.value,
                        footprint=symbol.footprint,
                        lib_id=symbol.lib_id,
                        description=symbol.description,
                        partnumber=symbol.partnumber,
                        fields_hidden={
                            "Description": symbol.is_hidden("Description"),
                            "PartNumber": symbol.is_hidden("PartNumber"),
                        },
                    )
                    comp = analyze_component(comp, self.config)
                    self.rows.append(self._row_from_schematic(comp))
        except Exception as exc:  # noqa: BLE001
            del cursor
            wx.MessageBox(f"扫描失败: {exc}", APP_TITLE, wx.OK | wx.ICON_ERROR, self)
            self._log(traceback.format_exc())
            return
        del cursor

        # 按「Value + 封装」分组：完全相同的元件只显示一行
        groups: Dict[Tuple[str, str, str, str], MatchRow] = {}
        unique: List[MatchRow] = []
        for row in self.rows:
            key = self._group_key(row.comp)
            primary = groups.get(key)
            if primary is not None:
                primary.members.append(row.comp)
            else:
                row.members = [row.comp]
                groups[key] = row
                unique.append(row)
        self.rows = unique
        for row in self.rows:
            self._refresh_group_status(row)
        counts = self._verify_existing()

        self._apply_sort()
        self._refresh_list()
        self._update_match_label()
        ready = sum(1 for r in self.rows if r.status == "已是最新")
        pending = sum(1 for r in self.rows if r.status == "未匹配")
        if verbose:
            self._log(f"扫描 {os.path.basename(root)}：共 {sum(r.member_count for r in self.rows)} 个元件、"
                      f"{len(self.rows)} 组（跳过电源符号 {skipped} 个），"
                      f"已有 PartNumber {ready} 组，待匹配 {pending} 组。")
            flagged = "，".join(f"{k} {v} 组" for k, v in counts.items() if v)
            if flagged:
                self._log(f"核对已有数据：{flagged}（红色=料号/描述有问题，橙色=不是最优料）。")

    def _apply_match(self, row: MatchRow) -> None:
        """对单行执行匹配，把结果写回该行（不动其他行）。"""
        matched = self._match_component(row.comp)
        row.candidates = matched.candidates
        row.selected = matched.selected
        row.pn = matched.pn
        row.description = matched.description
        row.status = matched.status
        row.note = matched.note

    # -- 核对原理图里已有的 PartNumber / 描述 ------------------------------
    @staticmethod
    def _desc_key(text: str) -> str:
        """描述比对用的归一化（忽略大小写/全角/空格/分隔符差异）。"""
        normalized = unicodedata.normalize("NFKC", text or "").upper()
        return re.sub(r"[\s,，、;；:：]+", "", normalized)

    def _verify_existing(self) -> Dict[str, int]:
        """扫描时核对已有料号/描述是否正确、是否最优。

        * 料号在 ERP 里找不到 / 描述与 ERP 不一致 / 料号不适配该元件 → 料号有误类状态（红）
        * 料号没问题但有更优的料 → 非最优（橙）
        """
        by_pn: Dict[str, Material] = {}
        for material in self.materials:
            pn = (material.pn or "").strip()
            if pn:
                by_pn[pn] = material
        counts = {"料号有误": 0, "描述不符": 0, "料号不匹配": 0, "非最优": 0}
        for row in self.rows:
            pn = (row.pn or "").strip()
            if not pn or row.status == "不上件跳过":
                continue
            material = by_pn.get(pn)
            if material is None:
                if not is_electronic_pn(pn):
                    # 「新料」占位、非电子料号等不判定为错误
                    continue
                row.status = "料号有误"
                row.note = f"ERP 物料表里找不到料号 {pn}"
                counts["料号有误"] += 1
                continue
            spec = (material.spec or "").strip()
            if row.description and self._desc_key(row.description) != self._desc_key(spec):
                row.status = "描述不符"
                row.note = f"ERP 里的描述是：{spec}"
                counts["描述不符"] += 1
                continue
            if not row.comp.kind:
                continue
            candidates = build_candidates(row.comp, self.materials, self.config,
                                          self.parsed_materials, index=self.matindex)
            row.candidates = candidates
            if not candidates:
                row.status = "料号不匹配"
                row.note = "该料号不适配此元件（规格不符），可右键「匹配此行」重新匹配"
                counts["料号不匹配"] += 1
                continue
            if pn not in {c.pn for c in candidates}:
                row.status = "料号不匹配"
                row.note = "该料号不适配此元件（规格不符），可右键「匹配此行」重新匹配"
                counts["料号不匹配"] += 1
                continue
            best = auto_select(candidates, self.config, row.comp.kind)
            if best is not None and best.pn != pn:
                row.status = "非最优"
                row.note = (f"更优的料：{best.pn} {best.description}"
                            f"（库存 {best.material.stock:g}）")
                counts["非最优"] += 1
        return counts

    def _kind_label(self, kind: str) -> str:
        return KIND_LABELS.get(kind, kind or "其他")

    def _match_kind_enabled(self, comp: Component) -> bool:
        return self.match_kinds.get(comp.kind or "", True)

    def _on_match_menu(self) -> None:
        """匹配类别下拉（弹出面板，勾选后不关闭）。"""
        kinds: List[str] = []
        for row in self.rows:
            key = row.comp.kind or ""
            if key not in kinds:
                kinds.append(key)
        if not kinds:
            wx.MessageBox("还没有元件，请先点「扫描」。", APP_TITLE,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        kinds.sort(key=lambda k: (k == "", k))
        items = [(k, self._kind_label(k), self.match_kinds.get(k, True)) for k in kinds]

        def toggle(key: str, checked: bool) -> None:
            self.match_kinds[key] = checked
            self._update_match_label(kinds)

        def on_all(value: bool) -> None:
            for k in kinds:
                self.match_kinds[k] = value
            self._update_match_label(kinds)

        popup = CheckPopup(self, "参与匹配的类别", items, toggle, on_all)
        popup.show_below(self.btn_match, align_right=True)

    def _update_match_label(self, kinds: Optional[List[str]] = None) -> None:
        if kinds is None:
            kinds = []
            for row in self.rows:
                key = row.comp.kind or ""
                if key not in kinds:
                    kinds.append(key)
        enabled = [k for k in kinds if self.match_kinds.get(k, True)]
        if not kinds or len(enabled) == len(kinds):
            self.btn_match.SetLabel("匹配")
        else:
            self.btn_match.SetLabel(f"匹配（{len(enabled)}/{len(kinds)} 类）")
        # 一个类别都没选时只禁主按钮，右侧 ▾ 保持可点（否则再也打不开了）
        self.btn_match.EnableAction(not kinds or bool(enabled))
        self.btn_match.SetToolTip(
            "左侧单击：开始匹配；右侧 ▾：选择要匹配的类别"
            "（当前：" + ("、".join(self._kind_label(k) for k in enabled) or "无") + "）")

    def _match_all(self, interactive: bool = True) -> None:
        """匹配：空行自动匹配；核对出问题的行（料号有误/描述不符/不适配）一并修复。"""
        self._ensure_materials()
        if not self.rows:
            if interactive:
                wx.MessageBox("还没有元件，请先点「扫描」。", APP_TITLE,
                              wx.OK | wx.ICON_INFORMATION, self)
            return
        redo = ("未匹配", "料号有误", "描述不符", "料号不匹配")
        todo = [r for r in self.rows
                if r.status in redo and self._match_kind_enabled(r.comp)]
        if not todo:
            if interactive:
                wx.MessageBox("没有需要匹配的元件（都已有 PartNumber 或标记为不上件）。",
                              APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return

        cursor = wx.BusyCursor()
        for row in todo:
            self._apply_match(row)
        del cursor

        self._apply_sort()
        self._refresh_list()
        matched = sum(1 for r in todo if r.status in ("已匹配", "已是最新"))
        new_items = sum(1 for r in todo if r.status == "新料")
        skipped = len(todo) - matched - new_items
        self._log(f"匹配 {len(todo)} 组：命中现有料 {matched} 组，新料 {new_items} 组"
                  + (f"，其它 {skipped} 组" if skipped else "") + "。")

    def _match_selected(self) -> None:
        """右键：重新匹配单行（会覆盖该行的 PartNumber/描述）。"""
        row = self._selected_row()
        if row is None:
            return
        cursor = wx.BusyCursor()
        self._apply_match(row)
        del cursor
        self._refresh_list()
        self._log(f"{row.references}: 重新匹配 → {row.status} {row.pn} {row.description}")

    def _group_key(self, comp: Component) -> Tuple[str, str, str, str]:
        """分组依据：类别 + 数值/型号 + 容差 + 封装。"""
        if comp.kind in ("R", "C", "L", "X", "XO") and comp.value_num is not None:
            value = f"{comp.kind}{comp.value_num:.6g}"
            if comp.kind == "L" and comp.current:
                value += f"/{comp.current:g}A"
        else:
            value = (comp.model or comp.value or "").strip().upper()
        tolerance = f"{comp.tolerance:g}" if comp.tolerance else ""
        return (comp.kind, value, tolerance, (comp.footprint or "").strip().upper())

    def _refresh_group_status(self, row: MatchRow) -> None:
        """整组都已写入目标值时才显示"已是最新"。"""
        if not row.pn:
            return
        all_current = True
        for comp in row.members:
            if row.value_edit and row.value_edit != (row.orig_value or comp.value):
                all_current = False
                break
            if row.pn != comp.partnumber:
                all_current = False
                break
            if row.description and row.description != comp.description:
                all_current = False
                break
            if not comp.hidden("PartNumber") or not comp.hidden("Description"):
                all_current = False
                break
        if all_current:
            row.status = "已是最新"
        elif row.status == "已是最新":
            # 内容已有但字段是可见的 / 需要重写，保留待写入状态
            row.status = "已匹配"

    def _match_component(self, comp: Component) -> MatchRow:
        row = MatchRow(comp=comp)

        if comp.do_not_place:
            row.status = "不上件跳过"
            row.note = "Value 为 NC/DNP，不上件"
            return row

        if not comp.kind:
            # 连接器 / 开关 / 晶振等未分类元件：按型号在物料表里找（区分贴片/插件）
            row.candidates = build_generic_candidates(comp, self.config, self.parsed_materials)
            if len(row.candidates) == 1:
                chosen = row.candidates[0]
                row.selected = chosen
                row.pn = chosen.pn
                row.description = chosen.description
                row.status = "已匹配"
                row.note = "按型号匹配"
            else:
                row.status = "未自动匹配"
                row.note = ("该类型不参与自动匹配（如连接器/开关），可手动搜索物料或右键生成新料"
                            if not row.candidates else "有多个同型号物料，请「选择候选…」")
            return row

        if comp.kind in ("R", "C") and comp.mount == "SMT" and not comp.size:
            row.status = "缺少封装"
            row.note = "无法从 Footprint 识别尺寸"
            return row

        if comp.kind == "LED" and not comp.size:
            row.status = "缺少封装"
            row.note = "无法从 Footprint 识别 LED 封装（如 0603 / 2835）"
            return row

        if comp.kind in ("R", "C", "L", "X", "XO") and comp.value_num is None:
            row.status = "无法识别数值"
            row.note = f"Value={comp.value}"
            return row

        if comp.kind in ("D", "IC", "Q", "MOS") and not comp.model:
            row.status = "缺少型号"
            return row

        row.candidates = build_candidates(comp, self.materials, self.config,
                                          self.parsed_materials, index=self.matindex)
        chosen = auto_select(row.candidates, self.config, comp.kind)
        if chosen is not None:
            row.selected = chosen
            row.pn = chosen.pn
            row.description = chosen.description
            row.status = "已匹配"
        else:
            pn, desc = build_new_item(comp, self.config)
            row.pn = pn
            row.description = desc
            row.status = "新料"
            row.note = "无可用现有料，建议新建"

        if (row.pn and row.pn == comp.partnumber
                and row.description and row.description == comp.description
                and comp.hidden("PartNumber") and comp.hidden("Description")):
            row.status = "已是最新"
        return row

    # -- 列表 -------------------------------------------------------------
    def _update_headers(self) -> None:
        for idx, title in enumerate(self._column_titles):
            text = title
            if idx == self.sort_column:
                text += " ▲" if self.sort_ascending else " ▼"
            item = wx.ListItem()
            item.SetText(text)
            # 列宽保持当前值（自适应由 _layout_columns 管），否则会把缩放后的宽度冲掉
            item.SetWidth(self.list.GetColumnWidth(idx) or self._column_widths[idx])
            self.list.SetColumn(idx, item)

    def _refresh_list(self, keep_view: bool = True) -> None:
        top = self.list.GetTopItem()
        selected = self.list.GetFirstSelected()
        self._update_headers()
        self.list.DeleteAllItems()
        for idx, row in enumerate(self.rows):
            self.list.InsertItem(idx, row.references)
            self.list.SetItem(idx, 1, row.comp.kind_label)
            self.list.SetItem(idx, 2, row.comp.value)
            self.list.SetItem(idx, 3, row.comp.size or row.comp.package or "-")
            self.list.SetItem(idx, 4, row.status)
            self.list.SetItem(idx, 5, row.pn)
            self.list.SetItem(idx, 6, row.description)
            self.list.SetItem(idx, 7, row.brand)
            self.list.SetItem(idx, 8, row.stock)
            self.list.SetItem(idx, 9, str(len(row.candidates)))
            if row.status in ("料号有误", "描述不符", "料号不匹配"):
                # 已有料号/描述与 ERP 对应不上 → 红色
                self.list.SetItemTextColour(idx, wx.Colour(200, 0, 0))
            elif row.status == "非最优":
                # 料号没问题，但不是最优选择 → 橙色
                self.list.SetItemTextColour(idx, wx.Colour(230, 140, 0))
            elif row.status == "新料":
                self.list.SetItemTextColour(idx, wx.Colour(200, 90, 0))
            elif row.status == "已匹配":
                self.list.SetItemTextColour(idx, wx.Colour(0, 120, 0))
            elif row.status == "手动":
                self.list.SetItemTextColour(idx, wx.Colour(0, 70, 180))
            elif row.status in ("未自动匹配", "缺少封装", "无法识别数值",
                                "缺少型号", "插件件跳过", "不上件跳过", "未匹配", "待处理"):
                self.list.SetItemTextColour(idx, wx.Colour(120, 120, 120))

        # 保持滚动位置与选中行，避免编辑后跳回顶部
        if keep_view and self.rows:
            last = len(self.rows) - 1
            selected = min(max(selected, 0), last)
            self.list.Select(selected)
            self.list.Focus(selected)
            if 0 <= top <= last:
                self.list.EnsureVisible(top)
            self.list.EnsureVisible(selected)

    # -- 排序 -------------------------------------------------------------
    @staticmethod
    def _natural(text: str) -> str:
        return re.sub(r"(\d+)", lambda m: m.group(1).zfill(6), (text or "").lower())

    def _sort_key(self, row: MatchRow, column: int):
        comp = row.comp
        if column == 0:
            return self._natural(row.references)
        if column == 1:
            return comp.kind_label
        if column == 2:
            return self._natural(comp.value)
        if column == 3:
            return comp.size or comp.package or ""
        if column == 4:
            return row.status
        if column == 5:
            return self._natural(row.pn)
        if column == 6:
            return self._natural(row.description)
        if column == 7:
            return row.brand
        if column == 8:
            try:
                return float(row.stock) if row.stock else -1.0
            except ValueError:
                return -1.0
        if column == 9:
            return len(row.candidates)
        return ""

    def _apply_sort(self) -> None:
        if self.sort_column < 0:
            return
        self.rows.sort(key=lambda r: self._sort_key(r, self.sort_column),
                       reverse=not self.sort_ascending)

    def _on_column_click(self, event) -> None:
        column = event.GetColumn()
        if column == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self._apply_sort()
        self._refresh_list()

    # -- 右键菜单 ---------------------------------------------------------
    def _on_right_click(self, event) -> None:
        index = event.GetIndex()
        if index < 0:
            return
        self.list.Select(index)
        row = self._selected_row()
        if row is None:
            return
        menu = wx.Menu()
        item_match = menu.Append(wx.ID_ANY, "匹配此行（重新匹配）")
        item_search = menu.Append(wx.ID_ANY, "搜索 ERP 物料…")
        item_candidate = menu.Append(wx.ID_ANY, "查看候选物料…")
        item_new = menu.Append(wx.ID_ANY, "生成新料描述…（可选类别）")
        item_manual = menu.Append(wx.ID_ANY, "编辑内容…（弹窗，一次改 Value/PN/描述）")
        item_clear = menu.Append(wx.ID_ANY, "清除 PartNumber / 描述")
        menu.AppendSeparator()
        item_copy = menu.Append(wx.ID_ANY, "复制位号")
        menu.Bind(wx.EVT_MENU, lambda e: self._match_selected(), item_match)
        menu.Bind(wx.EVT_MENU, lambda e: self._search_material(), item_search)
        menu.Bind(wx.EVT_MENU, lambda e: self._pick_candidate(), item_candidate)
        menu.Bind(wx.EVT_MENU, lambda e: self._generate_new_item(), item_new)
        menu.Bind(wx.EVT_MENU, lambda e: self._edit_row(), item_manual)
        menu.Bind(wx.EVT_MENU, lambda e: self._clear_row(), item_clear)
        menu.Bind(wx.EVT_MENU, lambda e: self._copy_references(), item_copy)
        self.list.PopupMenu(menu)
        menu.Destroy()

    def _generate_new_item(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        with NewItemDialog(self, row, self.config) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            pn, description = dlg.values()
        if not pn:
            pn = str(self.config.get("new_partnumber") or "新料")
        row.selected = None
        row.pn = pn
        row.description = description
        row.status = "手动"
        row.note = "按参数要求表生成"
        self._refresh_list()
        self._log(f"{row.references}: 生成新料 {pn} / {description}")

    def _manual_edit(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        with wx.TextEntryDialog(self, "物料编码（PartNumber）", "手动输入",
                                row.pn or "新料") as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            pn = dlg.GetValue().strip()
        with wx.TextEntryDialog(self, "描述（Description）", "手动输入",
                                row.description or "") as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            description = dlg.GetValue().strip()
        row.selected = None
        row.pn = pn
        row.description = description
        row.status = "手动" if pn else "待处理"
        row.note = "手动输入"
        self._refresh_list()

    def _clear_row(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        row.selected = None
        row.pn = ""
        row.description = ""
        row.status = "待处理"
        row.note = "已清除"
        self._refresh_list()

    def _copy_references(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(row.references))
            wx.TheClipboard.Close()
            self._log(f"已复制位号: {row.references}")

    def _selected_row(self) -> Optional[MatchRow]:
        index = self.list.GetFirstSelected()
        if index < 0 or index >= len(self.rows):
            return None
        return self.rows[index]

    def _activate_row(self, event=None) -> None:
        """双击单元格：就地把这一格变成输入框直接改（不弹新窗口）。"""
        index = event.GetIndex() if event is not None else self.list.GetFirstSelected()
        if index is None or index < 0:
            index = self.list.GetFirstSelected()
        pos = self.list.ScreenToClient(wx.GetMousePosition())
        hit_index, _flags = self.list.HitTest(pos)
        if hit_index == index and hit_index >= 0:
            column = self._column_at(pos)
        else:
            column = self._last_edit_column
        if column < 0:
            column = self._last_edit_column
        if column not in self.EDITABLE_COLUMNS:
            self._log(f"「{self._column_titles[column] if column < len(self._column_titles) else '?'}」"
                      "列不能直接编辑（可编辑：Value/型号、PartNumber、描述）。")
            return
        self._last_edit_column = column
        self._begin_inline_edit(index, column)

    # -- 单元格就地编辑 ----------------------------------------------------
    #: 可以直接双击编辑的列 → 字段名
    EDITABLE_COLUMNS = {2: "value", 5: "pn", 6: "description"}

    def _column_at(self, pos: wx.Point) -> int:
        """按 x 坐标算出点在哪个列（含水平滚动补偿）。"""
        x = pos.x + self.list.GetScrollPos(wx.HORIZONTAL)
        edge = 0
        for column in range(self.list.GetColumnCount()):
            edge += self.list.GetColumnWidth(column)
            if x < edge:
                return column
        return -1

    def _cell_rect(self, index: int, column: int) -> Optional[wx.Rect]:
        """算出某个单元格在 ListCtrl 客户区里的矩形。"""
        if index < 0 or index >= self.list.GetItemCount():
            return None
        row_rect = self.list.GetItemRect(index)
        if not row_rect.height:
            return None
        x = -self.list.GetScrollPos(wx.HORIZONTAL)
        for col in range(column):
            x += self.list.GetColumnWidth(col)
        width = self.list.GetColumnWidth(column)
        if width <= 0:
            return None
        return wx.Rect(x, row_rect.y, width, row_rect.height)

    def _cell_value(self, row: MatchRow, field: str) -> str:
        if field == "value":
            return row.comp.value or ""
        if field == "pn":
            return row.pn or ""
        return row.description or ""

    def _begin_inline_edit(self, index: int, column: int) -> None:
        field = self.EDITABLE_COLUMNS.get(column)
        if field is None or index < 0 or index >= len(self.rows):
            return
        self._commit_inline_edit()
        rect = self._cell_rect(index, column)
        if rect is None:
            return

        row = self.rows[index]
        self.list.Select(index)
        self.list.Focus(index)
        editor = wx.TextCtrl(self.list, value=self._cell_value(row, field),
                             style=wx.TE_PROCESS_ENTER)
        editor.SetFont(self.list.GetFont())
        editor.SetRect(rect)
        editor.SetInsertionPointEnd()
        editor.SetSelection(-1, -1)
        self._editor = editor
        self._editor_ctx = (row, field)
        editor.Bind(wx.EVT_TEXT_ENTER, lambda e: self._commit_inline_edit())
        editor.Bind(wx.EVT_KILL_FOCUS, lambda e: self._commit_inline_edit())
        editor.Bind(wx.EVT_KEY_DOWN, self._on_editor_key)
        editor.SetFocus()

    def _on_editor_key(self, event) -> None:
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self._cancel_inline_edit()
        elif key == wx.WXK_TAB:
            self._commit_inline_edit()
        else:
            event.Skip()

    def _cancel_inline_edit(self) -> None:
        editor = self._editor
        self._editor = None
        self._editor_ctx = None
        if editor is not None:
            editor.Destroy()
        self.list.SetFocus()

    def _commit_inline_edit(self) -> None:
        editor = self._editor
        ctx = self._editor_ctx
        if editor is None:
            return
        text = editor.GetValue()
        self._editor = None          # 先清空，避免 Destroy 触发 KILL_FOCUS 递归
        self._editor_ctx = None
        editor.Destroy()
        if ctx is None:
            return
        row, field = ctx
        self._apply_inline_edit(row, field, text.strip())

    def _apply_inline_edit(self, row: MatchRow, field: str, text: str) -> None:
        if text == self._cell_value(row, field):
            self._refresh_list()
            return
        if field == "value":
            if not text:
                self._refresh_list()
                return
            row.value_edit = text
            for comp in (row.members or [row.comp]):
                comp.value = text
                analyze_component(comp, self.config)
            if row.members:
                row.comp = row.members[0]
        elif field == "pn":
            row.selected = None
            row.pn = text
        else:
            row.selected = None
            row.description = text
        row.status = "手动" if (row.pn or row.description or row.value_edit) else "未匹配"
        row.note = "手动编辑"
        self._refresh_list()
        self._log(f"{row.references}: 手动修改 {self._FIELD_LABELS.get(field, field)}"
                  f" → {text or '（空）'}")

    #: 日志里的字段名
    _FIELD_LABELS = {"value": "Value", "pn": "PartNumber", "description": "描述"}

    def _edit_row(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        with EditRowDialog(self, row) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            value, pn, description = dlg.values()

        if value and value != row.comp.value:
            row.value_edit = value
            for comp in (row.members or [row.comp]):
                comp.value = value
                analyze_component(comp, self.config)
            if row.members:
                row.comp = row.members[0]

        row.pn = pn
        row.description = description
        row.status = "手动" if (pn or description or row.value_edit) else "未匹配"
        row.note = "手动编辑"
        self._refresh_list()
        self._log(f"{row.references}: 编辑 → Value={row.comp.value} PN={pn} 描述={description}")

    def _assign_material(self, row: MatchRow, material: Material) -> None:
        row.selected = None
        row.pn = material.pn
        row.description = material.spec
        row.status = "手动"
        row.note = "手动选择"
        # 让品牌 / 库存能回显（与「选择候选…」保持一致）
        parsed = self._parsed_by_pn.get(material.pn) or parse_material(material, self.config)
        row.selected = Candidate(parsed=parsed)

    def _pick_candidate(self) -> None:
        self._ensure_materials()
        row = self._selected_row()
        if row is None:
            wx.MessageBox("请先在列表中选择一个元件。", APP_TITLE,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        with CandidateDialog(self, row, self.materials) as dlg:
            result = dlg.ShowModal()
            if result != wx.ID_OK:
                return
            if dlg.choice is not None:
                row.selected = dlg.choice
                row.pn = dlg.choice.pn
                row.description = dlg.choice.description
                row.status = "手动"
            elif dlg.manual is not None:
                self._assign_material(row, dlg.manual)
            elif dlg.manual_pn:
                row.selected = None
                row.pn = dlg.manual_pn
                row.description = dlg.manual_desc
                row.status = "手动"
        self._refresh_list()

    def _search_material(self) -> None:
        self._ensure_materials()
        row = self._selected_row()
        if row is None:
            wx.MessageBox("请先在列表中选择一个元件。", APP_TITLE,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        keyword = search_keyword(row.comp)
        with MaterialSearchDialog(self, self.materials, keyword,
                                  f"为 {row.references} 搜索物料") as dlg:
            if dlg.ShowModal() != wx.ID_OK or dlg.material is None:
                return
            self._assign_material(row, dlg.material)
        self._refresh_list()

    def _show_rules(self) -> None:
        with RulesDialog(self) as dlg:
            dlg.ShowModal()

    # -- 写入 -------------------------------------------------------------
    def _on_write_menu(self) -> None:
        """写入范围下拉（弹出面板，勾选后不关闭）。"""
        labels = [("已匹配", "写入已匹配的物料"),
                  ("新料", "写入新料（PartNumber 写「新料」）"),
                  ("手动", "写入手动修改的")]
        items = [(key, label, self.write_filters.get(key, True)) for key, label in labels]

        def toggle(key: str, checked: bool) -> None:
            self.write_filters[key] = checked
            self._update_apply_label()

        def on_all(value: bool) -> None:
            for key in self.write_filters:
                self.write_filters[key] = value
            self._update_apply_label()

        popup = CheckPopup(self, "写入范围", items, toggle, on_all)
        popup.show_below(self.btn_apply, align_right=True)

    def _update_apply_label(self) -> None:
        enabled = [k for k, v in self.write_filters.items() if v]
        self.btn_apply.SetLabel("写入原理图" if len(enabled) == 3 else f"写入原理图（{'/'.join(enabled) or '无'}）")
        # 只禁主按钮，右侧 ▾ 保持可点，否则用户勾空了就再也打不开面板
        self.btn_apply.EnableAction(bool(enabled))
        self.btn_apply.SetToolTip(
            "左侧单击：写入原理图；右侧 ▾：选择写入范围"
            "（当前：" + ("、".join(enabled) or "未选择") + "）")

    def _write_enabled(self, row: MatchRow) -> bool:
        if row.status in ("已匹配", "非最优"):
            return self.write_filters.get("已匹配", True)
        if row.status == "新料":
            return self.write_filters.get("新料", True)
        if row.status == "手动":
            return self.write_filters.get("手动", True)
        return False

    def _apply(self) -> None:
        if not self.rows:
            wx.MessageBox("还没有元件结果，请先点「扫描」。", APP_TITLE,
                          wx.OK | wx.ICON_INFORMATION, self)
            return

        todo = [r for r in self.rows
                if r.pn and r.status != "已是最新"
                and self._write_enabled(r) and self._row_needs_update(r)]
        if not todo:
            wx.MessageBox("没有需要写入的改动。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return

        total_components = sum(r.member_count for r in todo)
        files = {c.file for r in todo for c in r.members}
        answer = wx.MessageBox(
            f"将写入 {len(todo)} 组 / {total_components} 个元件到 {len(files)} 个原理图文件。\n\n"
            "写入后会尝试让 KiCad 自动重新加载原理图；\n"
            "重新加载会丢弃内存里尚未保存的修改，请先按 Ctrl+S 保存。\n\n"
            "继续吗？",
            APP_TITLE, wx.YES_NO | wx.ICON_QUESTION, self)
        if answer != wx.YES:
            return

        by_file: Dict[str, List[Tuple[MatchRow, Component]]] = {}
        for row in todo:
            for comp in row.members:
                by_file.setdefault(comp.file, []).append((row, comp))

        written = 0
        failures: List[str] = []
        cursor = wx.BusyCursor()
        for path, entries in by_file.items():
            try:
                written += self._write_file(path, entries)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{os.path.basename(path)}: {exc}")
                self._log(traceback.format_exc())
        del cursor

        self._refresh_list()
        message = f"已写入 {written} 个元件。"
        if failures:
            message += "\n\n以下文件写入失败：\n" + "\n".join(failures)

        refreshed = False
        if written and not failures:
            try:
                refreshed, info = kicad_ui.refresh_schematic(
                    self.root_schematic or todo[0].members[0].file)
                message += f"\n\n{info}"
            except Exception as exc:  # noqa: BLE001
                self._log(traceback.format_exc())
                message += f"\n\n自动刷新失败: {exc}"
        if not refreshed:
            message += "\n\n请回到原理图窗口执行：文件 → 放弃变更（Revert）。"

        wx.MessageBox(message, APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
        self._log(message)

    def _row_needs_update(self, row: MatchRow) -> bool:
        for comp in row.members:
            if row.value_edit and row.value_edit != (row.orig_value or comp.value):
                return True
            if row.pn and row.pn != comp.partnumber:
                return True
            if row.description and row.description != comp.description:
                return True
            # 字段会显示在原理图上 → 需要重写为隐藏
            if not comp.hidden("PartNumber") or not comp.hidden("Description"):
                return True
        return False

    def _write_file(self, path: str, entries: Sequence[Tuple[MatchRow, Component]]) -> int:
        text = sch_sexpr.load_file(path)
        symbols = {s.uuid: s for s in sch_sexpr.read_symbols(text)}

        edits = []
        count = 0
        for row, comp in entries:
            symbol = symbols.get(comp.uuid)
            if symbol is None:
                continue
            changed = False
            if row.value_edit and row.value_edit != symbol.get_field("Value"):
                edits.append(sch_sexpr.plan_set_property(text, symbol, "Value", row.value_edit))
                changed = True
            if row.pn and (row.pn != symbol.get_field("PartNumber")
                           or not symbol.is_hidden("PartNumber")):
                edits.append(sch_sexpr.plan_set_property(text, symbol, "PartNumber", row.pn))
                changed = True
            if row.description and (row.description != symbol.get_field("Description")
                                    or not symbol.is_hidden("Description")):
                edits.append(sch_sexpr.plan_set_property(text, symbol, "Description", row.description))
                changed = True
            if changed:
                count += 1

        if not edits:
            return 0

        sch_sexpr.backup_file(path)
        sch_sexpr.write_file(path, sch_sexpr.apply_edits(text, edits))
        return count

    # -- 其它 -------------------------------------------------------------
    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.status.AppendText(f"[{stamp}] {message}\n")

    def _on_close(self, event: wx.CloseEvent) -> None:
        self._on_path_changed()
        # 记住窗口大小（全屏时记录退出前的尺寸）
        size = self._restore_size if self.IsFullScreen() and self._restore_size else self.GetSize()
        if not self.IsMaximized() and not self.IsFullScreen():
            self.config.set("window_size", [size.width, size.height])
        self.config.save()
        event.Skip()


def run() -> int:
    app = wx.App(False)
    dialog = ErpToSchDialog(None)
    dialog.Centre()
    dialog.ShowModal()
    dialog.Destroy()
    app.Destroy()
    return 0
