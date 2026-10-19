"""ERP 物料匹配插件主界面。"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import wx

import kicad_session
import sch_sexpr
from erp_config import Config
from erp_data import Material, load_materials, read_rules, rule_example, RuleRow
from erp_match import (
    Candidate,
    Component,
    analyze_component,
    auto_select,
    build_candidates,
    build_new_item,
)

APP_TITLE = "ERP 物料匹配（PartNumber / Description）"


# --------------------------------------------------------------------------
# 行数据
# --------------------------------------------------------------------------
@dataclass
class MatchRow:
    comp: Component
    status: str = "未匹配"
    candidates: List[Candidate] = field(default_factory=list)
    selected: Optional[Candidate] = None
    pn: str = ""
    description: str = ""
    note: str = ""

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
# 候选选择对话框
# --------------------------------------------------------------------------
class CandidateDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, row: MatchRow):
        super().__init__(parent, title=f"选择物料 - {row.comp.reference}", size=(900, 460))
        self.row = row
        self.choice: Optional[Candidate] = None
        self.manual_pn = ""
        self.manual_desc = ""

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        info = (f"位号 {row.comp.reference}    Value {row.comp.value}    "
                f"封装 {row.comp.footprint or '-'}")
        sizer.Add(wx.StaticText(panel, label=info), 0, wx.ALL, 8)

        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for idx, title in enumerate(["物料编码", "描述", "品牌", "库存", "优先级"]):
            self.list.InsertColumn(idx, title)
        self.list.SetColumnWidth(0, 140)
        self.list.SetColumnWidth(1, 470)
        self.list.SetColumnWidth(2, 90)
        self.list.SetColumnWidth(3, 80)
        self.list.SetColumnWidth(4, 90)

        for i, candidate in enumerate(row.candidates):
            self.list.InsertItem(i, candidate.pn)
            self.list.SetItem(i, 1, candidate.description)
            self.list.SetItem(i, 2, candidate.brand)
            self.list.SetItem(i, 3, f"{candidate.material.stock:g}")
            self.list.SetItem(i, 4, "优选" if candidate.brand_rank < 100 else
                              ("备选" if candidate.brand_rank < 1000 else "其他"))
        if row.candidates:
            self.list.Select(0)
        sizer.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(panel, wx.ID_OK, "使用选中物料")
        btn_manual = wx.Button(panel, label="手动输入…")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        buttons.Add(btn_manual, 0, wx.RIGHT, 8)
        buttons.AddStretchSpacer()
        buttons.Add(btn_cancel, 0, wx.RIGHT, 8)
        buttons.Add(btn_ok, 0)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(sizer)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(outer)

        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        btn_manual.Bind(wx.EVT_BUTTON, self._on_manual)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda evt: self._on_ok(None))

    def _on_ok(self, _event) -> None:
        index = self.list.GetFirstSelected()
        if index < 0 or index >= len(self.row.candidates):
            wx.MessageBox("请先选择一个物料。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return
        self.choice = self.row.candidates[index]
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
# 主对话框
# --------------------------------------------------------------------------
class ErpToSchDialog(wx.Dialog):
    def __init__(self, parent: Optional[wx.Window] = None):
        super().__init__(parent, title=APP_TITLE, size=(1240, 720))
        self.config = Config()
        self.materials: List[Material] = []
        self.rules: List[RuleRow] = []
        self.rows: List[MatchRow] = []
        self.schematic_files: List[str] = []
        self.project_dir = str(self.config.get("last_project_dir") or "")

        self._build_ui()
        self._detect_project()
        self._load_data(verbose=True)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        # 数据源
        grid = wx.FlexGridSizer(3, 3, 6, 8)
        grid.AddGrowableCol(1, 1)

        grid.Add(wx.StaticText(panel, label="ERP 物料表"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_erp = wx.TextCtrl(panel, value=self.config.erp_file)
        grid.Add(self.txt_erp, 1, wx.EXPAND)
        btn_erp = wx.Button(panel, label="浏览…")
        grid.Add(btn_erp, 0)

        grid.Add(wx.StaticText(panel, label="参数要求表"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_rules = wx.TextCtrl(panel, value=self.config.rules_file)
        grid.Add(self.txt_rules, 1, wx.EXPAND)
        btn_rules = wx.Button(panel, label="浏览…")
        grid.Add(btn_rules, 0)

        grid.Add(wx.StaticText(panel, label="工程目录"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_project = wx.TextCtrl(panel, value=self.project_dir)
        grid.Add(self.txt_project, 1, wx.EXPAND)
        btn_proj = wx.Button(panel, label="浏览…")
        grid.Add(btn_proj, 0)

        root.Add(grid, 0, wx.EXPAND | wx.ALL, 10)

        # 工具栏
        tools = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_reload = wx.Button(panel, label="重新加载物料表")
        self.btn_scan = wx.Button(panel, label="扫描并匹配")
        self.btn_pick = wx.Button(panel, label="选择候选…")
        self.btn_apply = wx.Button(panel, label="写入原理图")
        tools.Add(self.btn_reload, 0, wx.RIGHT, 8)
        tools.Add(self.btn_scan, 0, wx.RIGHT, 8)
        tools.Add(self.btn_pick, 0, wx.RIGHT, 8)
        tools.AddStretchSpacer()
        tools.Add(self.btn_apply, 0)
        root.Add(tools, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 表格
        self.list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        columns = [("位号", 110), ("类别", 60), ("Value", 110), ("封装", 80),
                   ("状态", 90), ("PartNumber", 140), ("描述", 470),
                   ("品牌", 80), ("库存", 70), ("候选", 60)]
        for idx, (title, width) in enumerate(columns):
            self.list.InsertColumn(idx, title, width=width)
        root.Add(self.list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 状态栏/日志
        self.status = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 90))
        root.Add(self.status, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(root)

        self.txt_erp.Bind(wx.EVT_KILL_FOCUS, lambda e: self._on_path_changed())
        btn_erp.Bind(wx.EVT_BUTTON, lambda e: self._browse_erp())
        btn_rules.Bind(wx.EVT_BUTTON, lambda e: self._browse_rules())
        btn_proj.Bind(wx.EVT_BUTTON, lambda e: self._browse_project())
        self.btn_reload.Bind(wx.EVT_BUTTON, lambda e: self._load_data(verbose=True, force=True))
        self.btn_scan.Bind(wx.EVT_BUTTON, lambda e: self._scan())
        self.btn_pick.Bind(wx.EVT_BUTTON, lambda e: self._pick_candidate())
        self.btn_apply.Bind(wx.EVT_BUTTON, lambda e: self._apply())
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._pick_candidate())
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # -- 数据源 -----------------------------------------------------------
    def _browse_erp(self) -> None:
        with wx.FileDialog(self, "选择 ERP 物料表", wildcard="Excel 文件 (*.xls;*.xlsx)|*.xls;*.xlsx",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.txt_erp.SetValue(dlg.GetPath())
                self._on_path_changed()
                self._load_data(verbose=True, force=True)

    def _browse_rules(self) -> None:
        with wx.FileDialog(self, "选择物料参数要求表", wildcard="Excel 文件 (*.xls;*.xlsx)|*.xls;*.xlsx",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.txt_rules.SetValue(dlg.GetPath())
                self._on_path_changed()
                self._load_data(verbose=True, force=False)

    def _browse_project(self) -> None:
        with wx.DirDialog(self, "选择 KiCad 工程目录", defaultPath=self.txt_project.GetValue() or "") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.txt_project.SetValue(dlg.GetPath())
                self._on_path_changed()

    def _on_path_changed(self) -> None:
        self.config.set("erp_file", self.txt_erp.GetValue().strip())
        self.config.set("rules_file", self.txt_rules.GetValue().strip())
        self.config.set("last_project_dir", self.txt_project.GetValue().strip())
        self.config.save()

    def _detect_project(self) -> None:
        filename, project_dir = kicad_session.get_open_schematic()
        if project_dir and os.path.isdir(project_dir):
            self.project_dir = project_dir
            self.txt_project.SetValue(project_dir)
            self.config.set("last_project_dir", project_dir)
            self.config.save()

        path = kicad_session.find_schematic(filename, [self.project_dir]) if filename else ""
        if path:
            self._log(f"检测到原理图: {path}")
        elif filename:
            self._log(f"KiCad 报告的原理图: {filename}（未找到完整路径，请选择工程目录）")
        else:
            self._log("未从 KiCad 获取到原理图（可手动选择工程目录，或在设置中启用 API）。")

    def _load_data(self, verbose: bool = True, force: bool = False) -> None:
        errors: List[str] = []
        cursor = wx.BusyCursor()

        erp = self.txt_erp.GetValue().strip()
        rules = self.txt_rules.GetValue().strip()
        try:
            if force or not self.materials:
                self.materials = load_materials(erp, self.config.cache_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"物料表读取失败: {exc}")
        try:
            self.rules = read_rules(rules) if rules else []
        except Exception as exc:  # noqa: BLE001
            errors.append(f"参数要求表读取失败: {exc}")

        del cursor

        if verbose:
            self._log(f"物料表: {len(self.materials)} 条")
            if self.rules:
                self._log(f"参数要求表: {len(self.rules)} 条")
        for message in errors:
            self._log(message)
            wx.MessageBox(message, APP_TITLE, wx.OK | wx.ICON_WARNING, self)

    # -- 扫描 / 匹配 -------------------------------------------------------
    def _scan(self) -> None:
        project = self.txt_project.GetValue().strip()
        filename, _ = kicad_session.get_open_schematic()

        root_file = ""
        if project and filename:
            root_file = kicad_session.find_schematic(filename, [project])
        if not root_file and project:
            root_file = kicad_session.find_schematic("", [project])
            if not root_file:
                candidates = sorted(
                    (p for p in os.listdir(project) if p.lower().endswith(".kicad_sch")),
                    key=lambda p: os.path.getmtime(os.path.join(project, p)),
                    reverse=True,
                )
                if candidates:
                    root_file = os.path.join(project, candidates[0])
        if not root_file:
            with wx.FileDialog(self, "选择原理图文件", wildcard="KiCad 原理图 (*.kicad_sch)|*.kicad_sch",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                root_file = dlg.GetPath()

        cursor = wx.BusyCursor()
        try:
            self.schematic_files = sch_sexpr.collect_schematics(root_file)
            self.rows = []
            skipped = 0
            for path in self.schematic_files:
                text = sch_sexpr.load_file(path)
                for symbol in sch_sexpr.read_symbols(text):
                    comp = Component(
                        file=path,
                        uuid=symbol.uuid,
                        reference=symbol.reference,
                        value=symbol.value,
                        footprint=symbol.footprint,
                        lib_id=symbol.lib_id,
                        description=symbol.description,
                        partnumber=symbol.partnumber,
                    )
                    comp = analyze_component(comp, self.config)
                    if not comp.kind or (comp.value_num is None and comp.kind != "D"):
                        skipped += 1
                        continue
                    self.rows.append(self._match_component(comp))
        except Exception as exc:  # noqa: BLE001
            del cursor
            wx.MessageBox(f"扫描失败: {exc}", APP_TITLE, wx.OK | wx.ICON_ERROR, self)
            self._log(traceback.format_exc())
            return
        del cursor

        self._refresh_list()
        matched = sum(1 for r in self.rows if r.selected)
        new_items = sum(1 for r in self.rows if r.status == "新料")
        self._log(f"扫描 {len(self.schematic_files)} 个原理图，可匹配元件 {len(self.rows)} 个"
                  f"（跳过 {skipped} 个非电阻/电容/二极管），命中 {matched}，新料 {new_items}。")

    def _match_component(self, comp: Component) -> MatchRow:
        row = MatchRow(comp=comp)

        footprint_lib = (comp.footprint or "").split(":")[0].lower()
        if "tht" in footprint_lib or "throughhole" in footprint_lib:
            row.status = "插件件跳过"
            row.note = "第一版只匹配贴片元件"
            return row

        # 电阻/电容必须有封装才能确定尺寸；没有就交给人工补全，不乱建料。
        if comp.kind in ("R", "C") and not comp.size:
            row.status = "缺少封装"
            row.note = "无法从 Footprint 识别尺寸"
            return row

        row.candidates = build_candidates(comp, self.materials, self.config)
        chosen = auto_select(row.candidates, self.config)
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
                and row.description and row.description == comp.description):
            row.status = "已是最新"
        return row

    # -- 列表 -------------------------------------------------------------
    def _refresh_list(self) -> None:
        self.list.DeleteAllItems()
        for idx, row in enumerate(self.rows):
            self.list.InsertItem(idx, row.comp.reference)
            kind_text = {"R": "电阻", "C": "电容", "D": "二极管"}.get(row.comp.kind, row.comp.kind)
            self.list.SetItem(idx, 1, kind_text)
            self.list.SetItem(idx, 2, row.comp.value)
            self.list.SetItem(idx, 3, row.comp.size or "-")
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

    def _selected_row(self) -> Optional[MatchRow]:
        index = self.list.GetFirstSelected()
        if index < 0 or index >= len(self.rows):
            return None
        return self.rows[index]

    def _pick_candidate(self) -> None:
        row = self._selected_row()
        if row is None:
            wx.MessageBox("请先在列表中选择一个元件。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return
        with CandidateDialog(self, row) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            if dlg.choice is not None:
                row.selected = dlg.choice
                row.pn = dlg.choice.pn
                row.description = dlg.choice.description
                row.status = "手动"
            elif dlg.manual_pn:
                row.selected = None
                row.pn = dlg.manual_pn
                row.description = dlg.manual_desc
                row.status = "手动"
        self._refresh_list()

    # -- 写入 -------------------------------------------------------------
    def _apply(self) -> None:
        if not self.rows:
            wx.MessageBox("还没有匹配结果，请先点击「扫描并匹配」。", APP_TITLE,
                          wx.OK | wx.ICON_INFORMATION, self)
            return

        todo = [r for r in self.rows
                if r.pn and r.status != "已是最新"
                and ((r.pn != r.comp.partnumber) or (r.description and r.description != r.comp.description))]
        if not todo:
            wx.MessageBox("没有需要写入的改动。", APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
            return

        answer = wx.MessageBox(
            f"将写入 {len(todo)} 个元件到 {len(set(r.comp.file for r in todo))} 个原理图文件。\n\n"
            "注意：\n"
            "1. 写入前会自动备份为 *.kicad_sch.erp-bak；\n"
            "2. 写入后请在 KiCad 中执行「文件 → 重新加载」；\n"
            "3. 重新加载前不要保存原理图，否则改动会被覆盖。\n\n"
            "继续吗？",
            APP_TITLE, wx.YES_NO | wx.ICON_QUESTION, self)
        if answer != wx.YES:
            return

        by_file: Dict[str, List[MatchRow]] = {}
        for row in todo:
            by_file.setdefault(row.comp.file, []).append(row)

        written = 0
        failures: List[str] = []
        cursor = wx.BusyCursor()
        for path, rows in by_file.items():
            try:
                written += self._write_file(path, rows)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{os.path.basename(path)}: {exc}")
                self._log(traceback.format_exc())
        del cursor

        self._refresh_list()
        message = f"已写入 {written} 个元件。"
        if failures:
            message += "\n\n以下文件写入失败：\n" + "\n".join(failures)
        message += "\n\n请回到原理图窗口执行：文件 → 重新加载（Revert）。"
        wx.MessageBox(message, APP_TITLE, wx.OK | wx.ICON_INFORMATION, self)
        self._log(message)

    def _write_file(self, path: str, rows: Sequence[MatchRow]) -> int:
        text = sch_sexpr.load_file(path)
        symbols = {s.uuid: s for s in sch_sexpr.read_symbols(text)}

        edits = []
        count = 0
        for row in rows:
            symbol = symbols.get(row.comp.uuid)
            if symbol is None:
                continue
            changed = False
            if row.pn and row.pn != symbol.get_field("PartNumber"):
                edits.append(sch_sexpr.plan_set_property(text, symbol, "PartNumber", row.pn))
                changed = True
            if row.description and row.description != symbol.get_field("Description"):
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


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run() -> int:
    app = wx.App(False)
    dialog = ErpToSchDialog(None)
    dialog.Centre()
    dialog.ShowModal()
    dialog.Destroy()
    app.Destroy()
    return 0
