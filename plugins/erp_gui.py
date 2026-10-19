"""ERP 物料匹配插件主界面。"""

from __future__ import annotations

import os
import re
import time
import traceback
from dataclasses import dataclass, field
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
    is_tht_footprint,
    parse_material,
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
        super().__init__(parent, title=title, size=(980, 560))
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
        top.Add(self.chk_disabled, 0, wx.ALIGN_CENTER_VERTICAL)
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
        keys = [k for k in keyword.split() if k]

        results: List[Material] = []
        for material in self.materials:
            if material.disabled and not include_disabled:
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
        super().__init__(parent, title=f"选择物料 - {row.comp.reference}", size=(980, 500))
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
        for i, candidate in enumerate(row.candidates):
            self.list.InsertItem(i, candidate.pn)
            self.list.SetItem(i, 1, candidate.description)
            self.list.SetItem(i, 2, candidate.brand)
            self.list.SetItem(i, 3, f"{candidate.material.stock:g}")
            self.list.SetItem(i, 4, "优选" if candidate.brand_rank < 100 else
                              ("备选" if candidate.brand_rank < 1000 else "其他"))
            if candidate.material.disabled:
                self.list.SetItemTextColour(i, wx.Colour(150, 150, 150))
        if row.candidates:
            self.list.Select(0)
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

    def _on_ok(self, _event) -> None:
        index = self.list.GetFirstSelected()
        if index < 0 or index >= len(self.row.candidates):
            wx.MessageBox("请先选择一个物料。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return
        self.choice = self.row.candidates[index]
        self.EndModal(wx.ID_OK)

    def _on_search(self, _event) -> None:
        keyword = self.row.comp.model or self.row.comp.value
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
        super().__init__(parent, title="物料参数要求（内置）", size=(760, 520))
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
        super().__init__(parent, title=f"编辑元件 - {row.references}", size=(720, 380))
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
        super().__init__(parent, title=f"生成新料描述 - {row.references}", size=(860, 460))
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
# 主对话框
# --------------------------------------------------------------------------
class ErpToSchDialog(wx.Dialog):
    def __init__(self, parent: Optional[wx.Window] = None):
        super().__init__(parent, title=APP_TITLE, size=(1280, 760))
        self.config = Config()
        self.materials: List[Material] = []
        self.parsed_materials: List[ParsedMaterial] = []
        self.rows: List[MatchRow] = []
        self.schematic_files: List[str] = []
        self.root_schematic = ""
        self.sort_column = -1
        self.sort_ascending = True
        self._column_titles: List[str] = []
        self._column_widths: List[int] = []
        #: 写入范围（默认全选）
        self.write_filters: Dict[str, bool] = {"已匹配": True, "新料": True, "手动": True}
        #: 参与自动匹配的类别（默认全选）
        self.match_kinds: Dict[str, bool] = {}

        self._build_ui()
        self._detect_project()
        self._load_data(verbose=True)
        # 打开插件先自动扫描（只读，不改任何东西）
        wx.CallAfter(self._auto_scan)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(2, 3, 6, 8)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(panel, label="ERP 物料表"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_erp = wx.TextCtrl(panel, value=self.config.erp_file)
        grid.Add(self.txt_erp, 1, wx.EXPAND)
        btn_erp = wx.Button(panel, label="浏览…")
        grid.Add(btn_erp, 0)

        grid.Add(wx.StaticText(panel, label="工程目录"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_project = wx.TextCtrl(panel)
        grid.Add(self.txt_project, 1, wx.EXPAND)
        btn_proj = wx.Button(panel, label="浏览…")
        grid.Add(btn_proj, 0)
        root.Add(grid, 0, wx.EXPAND | wx.ALL, 10)

        tools = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reload = wx.Button(panel, label="重新加载物料表")
        self.btn_scan = wx.Button(panel, label="扫描")
        self.btn_match_opts = wx.Button(panel, label="匹配类别 ▾", style=wx.BU_EXACTFIT)
        self.btn_match = wx.Button(panel, label="匹配")
        self.btn_pick = wx.Button(panel, label="选择候选…")
        self.btn_search = wx.Button(panel, label="搜索物料…")
        self.btn_rules = wx.Button(panel, label="参数要求…")
        for button in (self.btn_reload, self.btn_scan, self.btn_match_opts, self.btn_match,
                       self.btn_pick, self.btn_search, self.btn_rules):
            tools.Add(button, 0, wx.RIGHT, 8)
        tools.AddStretchSpacer()
        self.btn_apply = wx.Button(panel, label="写入原理图")
        self.btn_apply_opts = wx.Button(panel, label="写入范围 ▾", style=wx.BU_EXACTFIT)
        tools.Add(self.btn_apply, 0, wx.RIGHT, 4)
        tools.Add(self.btn_apply_opts, 0)
        root.Add(tools, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

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
        self.Layout()

        self.txt_erp.Bind(wx.EVT_KILL_FOCUS, lambda e: self._on_path_changed())
        btn_erp.Bind(wx.EVT_BUTTON, lambda e: self._browse_erp())
        btn_proj.Bind(wx.EVT_BUTTON, lambda e: self._browse_project())
        self.btn_reload.Bind(wx.EVT_BUTTON, lambda e: self._load_data(verbose=True, force=True))
        self.btn_scan.Bind(wx.EVT_BUTTON, lambda e: self._scan())
        self.btn_match.Bind(wx.EVT_BUTTON, lambda e: self._match_all())
        self.btn_match_opts.Bind(wx.EVT_BUTTON, lambda e: self._on_match_menu())
        self.btn_pick.Bind(wx.EVT_BUTTON, lambda e: self._pick_candidate())
        self.btn_search.Bind(wx.EVT_BUTTON, lambda e: self._search_material())
        self.btn_rules.Bind(wx.EVT_BUTTON, lambda e: self._show_rules())
        self.btn_apply.Bind(wx.EVT_BUTTON, lambda e: self._apply())
        self.btn_apply_opts.Bind(wx.EVT_BUTTON, lambda e: self._on_write_menu())
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._activate_row())
        self.list.Bind(wx.EVT_LIST_COL_CLICK, self._on_column_click)
        self.list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_right_click)
        self.Bind(wx.EVT_CLOSE, self._on_close)

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
                self._on_path_changed()

    def _on_path_changed(self) -> None:
        self.config.set("erp_file", self.txt_erp.GetValue().strip())
        self.config.set("last_project_dir", self.txt_project.GetValue().strip())
        self.config.save()

    def _detect_project(self) -> None:
        filename, project_dir = kicad_session.get_open_schematic()
        if project_dir and os.path.isdir(project_dir):
            self.txt_project.SetValue(project_dir)
        elif filename:
            path = kicad_session.find_schematic(
                filename, [self.config.get("last_project_dir") or ""])
            if path:
                self.txt_project.SetValue(os.path.dirname(path))
        if not self.txt_project.GetValue():
            for folder in (kicad_session.process_hint_dirs()
                           + kicad_session.open_project_dirs()):
                root = kicad_session.find_root_schematic(folder)
                if root:
                    self.txt_project.SetValue(os.path.dirname(root))
                    break
        if self.txt_project.GetValue():
            self.config.set("last_project_dir", self.txt_project.GetValue())
            self.config.save()
            self._log(f"工程目录: {self.txt_project.GetValue()}")
        else:
            self._log("未自动检测到工程目录（扫描时会再尝试一次）。")

    def _load_data(self, verbose: bool = True, force: bool = False) -> None:
        erp = self.txt_erp.GetValue().strip()
        cursor = wx.BusyCursor()
        try:
            if force or not self.materials:
                self.materials = load_materials(erp, self.config.cache_dir)
                self.parsed_materials = [parse_material(m, self.config)
                                         for m in self.materials if not m.disabled]
        except Exception as exc:  # noqa: BLE001
            del cursor
            self._log(f"物料表读取失败: {exc}")
            wx.MessageBox(f"物料表读取失败:\n{exc}", APP_TITLE, wx.OK | wx.ICON_WARNING, self)
            return
        del cursor
        if verbose:
            self._log(f"物料表: {len(self.materials)} 条")

    # -- 扫描 -------------------------------------------------------------
    def _resolve_root_schematic(self) -> str:
        filename, ipc_dir = kicad_session.get_open_schematic()
        project = self.txt_project.GetValue().strip()

        dirs: List[str] = []
        for d in (project, ipc_dir):
            if d and d not in dirs and os.path.isdir(d):
                dirs.append(d)
        for d in kicad_session.process_hint_dirs() + kicad_session.open_project_dirs():
            if d not in dirs and os.path.isdir(d):
                dirs.append(d)

        if filename:
            for d in dirs:
                candidate = os.path.join(d, filename)
                if os.path.isfile(candidate):
                    return os.path.abspath(candidate)

        for d in dirs:
            root = kicad_session.find_root_schematic(d)
            if root:
                return root
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

    def _scan(self, interactive: bool = True, verbose: bool = True) -> None:
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

        self._apply_sort()
        self._refresh_list()
        self._update_match_label()
        ready = sum(1 for r in self.rows if r.status == "已是最新")
        pending = sum(1 for r in self.rows if r.status == "未匹配")
        if verbose:
            self._log(f"扫描 {os.path.basename(root)}：共 {sum(r.member_count for r in self.rows)} 个元件、"
                      f"{len(self.rows)} 组（跳过电源符号 {skipped} 个），"
                      f"已有 PartNumber {ready} 组，待匹配 {pending} 组。")

    def _apply_match(self, row: MatchRow) -> None:
        """对单行执行匹配，把结果写回该行（不动其他行）。"""
        matched = self._match_component(row.comp)
        row.candidates = matched.candidates
        row.selected = matched.selected
        row.pn = matched.pn
        row.description = matched.description
        row.status = matched.status
        row.note = matched.note

    def _kind_label(self, kind: str) -> str:
        return KIND_LABELS.get(kind, kind or "其他")

    def _match_kind_enabled(self, comp: Component) -> bool:
        return self.match_kinds.get(comp.kind or "", True)

    def _on_match_menu(self) -> None:
        """匹配类别下拉：勾选要参与自动匹配的元件类别。"""
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
        menu = wx.Menu()
        id_to_key: Dict[int, str] = {}
        for key in kinds:
            item = menu.AppendCheckItem(wx.ID_ANY, self._kind_label(key))
            item.Check(self.match_kinds.get(key, True))
            id_to_key[item.GetId()] = key
        menu.AppendSeparator()
        item_all = menu.Append(wx.ID_ANY, "全选")

        def on_choose(event) -> None:
            key = id_to_key.get(event.GetId())
            if key is not None:
                self.match_kinds[key] = not self.match_kinds.get(key, True)
            elif event.GetId() == item_all.GetId():
                for k in kinds:
                    self.match_kinds[k] = True
            self._update_match_label(kinds)

        menu.Bind(wx.EVT_MENU, on_choose)
        pos = self.btn_match_opts.ClientToScreen(wx.Point(0, self.btn_match_opts.GetSize().height))
        self.PopupMenu(menu, self.ScreenToClient(pos))
        menu.Destroy()

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
        self.btn_match_opts.SetToolTip(
            "选择要匹配的类别：" + ("、".join(self._kind_label(k) for k in enabled) or "无"))

    def _match_all(self, interactive: bool = True) -> None:
        """只匹配还没有 PartNumber 的行；已有数据的一律不动。"""
        if not self.rows:
            if interactive:
                wx.MessageBox("还没有元件，请先点「扫描」。", APP_TITLE,
                              wx.OK | wx.ICON_INFORMATION, self)
            return
        todo = [r for r in self.rows
                if r.status == "未匹配" and self._match_kind_enabled(r.comp)]
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
                row.note = ("该类型不参与自动匹配（如 LED/连接器），可手动搜索物料或右键生成新料"
                            if not row.candidates else "有多个同型号物料，请「选择候选…」")
            return row

        if comp.kind in ("R", "C") and comp.mount == "SMT" and not comp.size:
            row.status = "缺少封装"
            row.note = "无法从 Footprint 识别尺寸"
            return row

        if comp.kind in ("R", "C", "L", "X", "XO") and comp.value_num is None:
            row.status = "无法识别数值"
            row.note = f"Value={comp.value}"
            return row

        if comp.kind in ("D", "IC", "Q", "MOS") and not comp.model:
            row.status = "缺少型号"
            return row

        row.candidates = build_candidates(comp, self.materials, self.config,
                                          self.parsed_materials)
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
            item.SetWidth(self._column_widths[idx])
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
            if row.status == "新料":
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
        item_manual = menu.Append(wx.ID_ANY, "编辑内容…（Value / PartNumber / 描述）")
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

    def _activate_row(self) -> None:
        """双击行：编辑内容（Value / PartNumber / 描述）。"""
        self._edit_row()

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

    def _pick_candidate(self) -> None:
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
        row = self._selected_row()
        if row is None:
            wx.MessageBox("请先在列表中选择一个元件。", APP_TITLE,
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        keyword = row.comp.model or row.comp.value
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
        """写入范围下拉：勾选已匹配 / 新料 / 手动修改。"""
        menu = wx.Menu()
        labels = {"已匹配": "写入已匹配的物料",
                  "新料": "写入新料（PartNumber 写「新料」）",
                  "手动": "写入手动修改的"}
        id_to_key: Dict[int, str] = {}
        for key, label in labels.items():
            item = menu.AppendCheckItem(wx.ID_ANY, label)
            item.Check(self.write_filters.get(key, True))
            id_to_key[item.GetId()] = key
        menu.AppendSeparator()
        item_all = menu.Append(wx.ID_ANY, "全选")

        def on_choose(event) -> None:
            key = id_to_key.get(event.GetId())
            if key:
                self.write_filters[key] = not self.write_filters[key]
            elif event.GetId() == item_all.GetId():
                for k in self.write_filters:
                    self.write_filters[k] = True
            self._update_apply_label()

        menu.Bind(wx.EVT_MENU, on_choose)
        pos = self.btn_apply_opts.ClientToScreen(wx.Point(0, self.btn_apply_opts.GetSize().height))
        self.PopupMenu(menu, self.ScreenToClient(pos))
        menu.Destroy()

    def _update_apply_label(self) -> None:
        enabled = [k for k, v in self.write_filters.items() if v]
        self.btn_apply.SetLabel("写入原理图" if len(enabled) == 3 else f"写入原理图（{'/'.join(enabled) or '无'}）")
        self.btn_apply.Enable(bool(enabled))
        self.btn_apply_opts.SetToolTip("选择写入范围：" + ("、".join(enabled) or "未选择"))

    def _write_enabled(self, row: MatchRow) -> bool:
        if row.status == "已匹配":
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
        event.Skip()


def run() -> int:
    app = wx.App(False)
    dialog = ErpToSchDialog(None)
    dialog.Centre()
    dialog.ShowModal()
    dialog.Destroy()
    app.Destroy()
    return 0
