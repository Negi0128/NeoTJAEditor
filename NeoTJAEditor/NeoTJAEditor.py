import tkinter as tk
from tkinter import filedialog, messagebox, font, Toplevel
from tkinter import ttk
import re
import subprocess
import os
import sys
import json
import math
import webbrowser
from decimal import Decimal, getcontext
try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

APP_NAME = "NeoTJAEditor"
VERSION  = "4.0.0"

getcontext().prec = 50
SETTINGS_FILE = "settings.json"

NEW_FILE_TEMPLATE = """\
TITLE:
SUBTITLE:--
BPM:
WAVE:
OFFSET:0.00
SONGVOL:100
SEVOL:100
DEMOSTART:0.00

COURSE:oni
LEVEL:
BALLOON:
SCOREINIT:
SCOREDIFF:

#START

#END
"""

VALID_MEASURE_COUNTS = {1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96, 128, 192, 256}

THEMES = {
    "dark": {
        "bg":            "#0f1117",
        "bg2":           "#161b27",
        "bg3":           "#1e2535",
        "surface":       "#252d3d",
        "border":        "#2e3a50",
        "accent":        "#4f9cf9",
        "accent2":       "#f97c4f",
        "fg":            "#cdd6f4",
        "fg_dim":        "#6c7a96",
        "fg_bright":     "#ffffff",
        "don":           "#ff6b6b",
        "ka":            "#5bc8fc",
        "roll":          "#ffd166",
        "balloon":       "#ff9f43",
        "cmd":           "#a29bfe",
        "header_key":    "#74b9ff",
        "header_val":    "#55efc4",
        "comment":       "#4a5568",
        "zero":          "#2e3a50",
        "comma":         "#3d4f6b",
        "cursor":        "#4f9cf9",
        "select":        "#2a3f6f",
        "warn":          "#9d7b00",
        "err":           "#ff6b6b",
        "ok":            "#00b894",
        "checkpoint":    "#ffd166",
        "toolbar_btn":   "#1e2535",
        "toolbar_hover": "#2e3a50",
    },
    "light": {
        "bg":            "#f8f9fa",
        "bg2":           "#e9ecef",
        "bg3":           "#dee2e6",
        "surface":       "#ffffff",
        "border":        "#ced4da",
        "accent":        "#0d6efd",
        "accent2":       "#fd7e14",
        "fg":            "#212529",
        "fg_dim":        "#6c757d",
        "fg_bright":     "#000000",
        "don":           "#dc3545",
        "ka":            "#0dcaf0",
        "roll":          "#ffc107",
        "balloon":       "#fd7e14",
        "cmd":           "#6f42c1",
        "header_key":    "#0d6efd",
        "header_val":    "#20c997",
        "comment":       "#adb5bd",
        "zero":          "#ced4da",
        "comma":         "#adb5bd",
        "cursor":        "#0d6efd",
        "select":        "#cfe2ff",
        "warn":          "#ffed4a",
        "err":           "#dc3545",
        "ok":            "#198754",
        "checkpoint":    "#fd7e14",
        "toolbar_btn":   "#ffffff",
        "toolbar_hover": "#e2e6ea",
    }
}

COLORS = THEMES["dark"].copy()

# ==============================================================================
#  ツールチップ
# ==============================================================================
class ToolTip:
    def __init__(self, widget):
        self.widget = widget
        self.tw = None

    def show(self, text, event):
        self.hide()
        x = event.x_root + 16
        y = event.y_root - 36
        self.tw = tw = Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=text, justify=tk.LEFT,
                 background=COLORS["surface"], foreground=COLORS["fg"],
                 relief="solid", borderwidth=1, highlightbackground=COLORS["border"],
                 padx=8, pady=4, font=("Consolas", 9)).pack()

    def hide(self):
        if self.tw:
            self.tw.destroy()
            self.tw = None

# ==============================================================================
#  ヘルプウィンドウ
# ==============================================================================
class HelpWindow(Toplevel):
    CHAPTERS = {
        "譜面の仕組み": (
            "TJA形式の譜面は 0〜9 の数字とカンマで構成される。\n\n"
            "0 : 休符    1 : ドン    2 : カッ\n"
            "3 : 大ドン  4 : 大カッ  5 : 連打開始\n"
            "6 : 大連打  7 : 風船    8 : 連打/風船終了\n"
            "9 : 芋音符\n\n"
            "カンマ ( , ) が 1 小節の終わりを表す。"
        ),
        "ショートカット": (
            "Ctrl+S        上書き保存\n"
            "Ctrl+A        全選択\n"
            "Ctrl+T        切り取り\n"
            "Ctrl+Z        元に戻す\n"
            "Ctrl+Y        やり直す\n\n"
            "Alt+P         チェックポイント設定/解除\n"
            "Ctrl+↑/↓      前後のチェックポイントへ移動\n"
            "Ctrl+ホイール  フォントサイズ変更\n"
            "Ctrl+M        あべこべ反転\n\n"
            "Alt+0〜9      カスタムショートカット挿入\n"
            "Alt+B  #BPMCHANGE    Alt+S  #SCROLL\n"
            "Alt+D  #DELAY        Alt+U  #MEASURE\n"
            "Alt+G  #GOGOSTART    Alt+O  #GOGOEND\n"
            "Alt+L  #BARLINEON    Alt+I  #BARLINEOFF\n"
            "Alt+R  #BRANCHSTART"
        ),
        "ツール": (
            "メニュー「ツール」から利用できる。\n\n"
            "■ ハイスピ変換 / リサイズ\n"
            "  選択範囲のノーツ間隔やスクロール速度を変換する。\n\n"
            "■ ストロボ生成\n"
            "  カーソル位置に指定FPS・BPMで静止するギミックを生成する。\n\n"
            "■ あべこべ反転 (Ctrl+M)\n"
            "  ドン(1,3)とカッ(2,4)を入れ替える。"
             "■ 譜面画像生成(試験的)\n"
            "  譜面画像を自動で生成する。"
        ),
        "エラー診断": (
            "■ 行番号の「!」マーク\n"
            "  1小節の文字数が不正な行を警告する。\n\n"
            "■ 背景の黄色ハイライト\n"
            "  #SCROLL が空行やノーツを挟まずに連続して重複している箇所をハイライト。\n\n"
            "■ ステータスバー（画面下部）\n"
            "  風船数やSTART/END、GOGOSTART/ENDの不一致を警告する。"
        ),
        "バグ報告": "__link__",
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.title("NeoTJAEditor ヘルプ")
        self.geometry("680x480")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)

        pw = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=3, bg=COLORS["border"])
        pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        lb = tk.Listbox(pw, font=("Consolas", 10), width=22, bg=COLORS["bg2"], fg=COLORS["fg"],
                        selectbackground=COLORS["accent"], selectforeground=COLORS["fg_bright"],
                        borderwidth=0, highlightthickness=0)
        pw.add(lb)

        txt = tk.Text(pw, font=("Consolas", 10), wrap=tk.WORD, padx=14, pady=14, bg=COLORS["bg3"], fg=COLORS["fg"],
                      borderwidth=0, highlightthickness=0, insertbackground=COLORS["cursor"])
        pw.add(txt)

        self._lb = lb
        self._txt = txt

        for ch in self.CHAPTERS:
            lb.insert(tk.END, f"  {ch}")
        
        lb.bind("<<ListboxSelect>>", self._select)
        lb.selection_set(0)
        self._select()

    def _select(self, event=None):
        sel = self._lb.curselection()
        if not sel: return
        key = list(self.CHAPTERS.keys())[sel[0]]
        body = self.CHAPTERS[key]

        self._txt.config(state="normal")
        self._txt.delete("1.0", tk.END)
        self._txt.insert(tk.END, f"{key}\n\n", "h")
        self._txt.tag_config("h", font=("Consolas", 13, "bold"), foreground=COLORS["accent"])

        if body == "__link__":
            self._txt.insert(tk.END, "NeoTJAEditor をご利用いただきありがとうございます。\nバグ報告・要望は開発者NegiのDMにお願いします。\n\n")
            self._txt.insert(tk.END, "@n_enu_taiko を開く", "link")
            self._txt.tag_config("link", foreground=COLORS["accent"], underline=True)
            self._txt.tag_bind("link", "<Button-1>", lambda e: webbrowser.open("https://x.com/n_enu_taiko"))
            self._txt.tag_bind("link", "<Enter>", lambda e: self._txt.config(cursor="hand2"))
            self._txt.tag_bind("link", "<Leave>", lambda e: self._txt.config(cursor=""))
        else:
            self._txt.insert(tk.END, body)
        self._txt.config(state="disabled")

# ==============================================================================
#  ストロボ生成ダイアログ
# ==============================================================================
class StrobeGeneratorDialog(Toplevel):
    def __init__(self, editor_app, initial_bpm, apply_cb):
        super().__init__(editor_app.master)
        self.editor_app = editor_app
        self.title("ストロボ生成")
        self.geometry("600x700")
        self.configure(bg=COLORS["bg"])
        self.apply_cb = apply_cb
        self.transient(editor_app.master)
        self.grab_set()

        self.v_fps = tk.StringVar(value="120")
        self.v_bpm = tk.StringVar(value=initial_bpm)
        self.v_length = tk.StringVar(value="1小節")
        
        self.v_start = tk.StringVar(value="90.0")
        self.v_end   = tk.StringVar(value="90.0")
        self.v_curve = tk.StringVar(value="直線 (Linear)")
        self.v_prec  = tk.StringVar(value="3")

        self._build()
        self._preview()

    def _build(self):
        frm = tk.LabelFrame(self, text=" パラメータ ", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9), bd=1, relief="flat", highlightbackground=COLORS["border"], highlightthickness=1)
        frm.pack(fill="x", padx=12, pady=8)

        def row(r, label, var, is_combo=False, values=None, is_spin=False):
            tk.Label(frm, text=label, bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=r, column=0, sticky="e", padx=8, pady=6)
            if is_combo:
                w = ttk.Combobox(frm, textvariable=var, values=values, state="readonly", width=26)
            elif is_spin:
                w = tk.Spinbox(frm, from_=1, to=5, textvariable=var, width=6, bg=COLORS["surface"], fg=COLORS["fg"], buttonbackground=COLORS["surface"], highlightthickness=0)
            else:
                w = tk.Entry(frm, textvariable=var, width=14, bg=COLORS["surface"], fg=COLORS["fg"], insertbackground=COLORS["cursor"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["border"])
            w.grid(row=r, column=1, sticky="w", padx=8, pady=6)
            return w

        row(0, "再生シミュレーターFPS", self.v_fps, is_combo=True, values=("60", "120", "144", "240"))
        row(1, "基準BPM", self.v_bpm)
        row(2, "生成長さ", self.v_length, is_combo=True, values=("1/8小節", "1/4小節", "1/2小節", "1小節"))
        row(3, "開始 SCROLL", self.v_start)
        row(4, "終了 SCROLL", self.v_end)
        row(5, "変化カーブ", self.v_curve, is_combo=True, values=("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)"))
        row(6, "小数点以下", self.v_prec, is_spin=True)

        for v in (self.v_fps, self.v_bpm, self.v_length, self.v_start, self.v_end, self.v_curve, self.v_prec):
            v.trace_add("write", self._preview)

        tk.Label(self, text="▸ プレビュー", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9)).pack(anchor="w", padx=12, pady=(10, 2))
        
        bf = tk.Frame(self, bg=COLORS["bg"])
        bf.pack(side=tk.BOTTOM, fill="x", pady=10)
        tk.Button(bf, text="キャンセル", command=self.destroy, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10), relief="flat", padx=14, pady=6).pack(side="right", padx=8)
        tk.Button(bf, text="エディタに挿入", command=self._apply, bg=COLORS["accent"], fg=COLORS["fg_bright"], font=("Consolas", 10, "bold"), relief="flat", padx=14, pady=6).pack(side="right")

        self.txt_after = tk.Text(self, font=("Consolas", 11), bg=COLORS["bg2"], fg=COLORS["fg"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["accent"])
        self.txt_after.pack(side=tk.TOP, fill="both", expand=True, padx=12, pady=4)

    def _get_curve_val(self, t, curve_type):
        if "加速" in curve_type: return t * t
        elif "減速" in curve_type: return 1 - (1 - t) ** 2
        elif "S字" in curve_type: return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
        return t

    def _preview(self, *_):
        try:
            fps = int(self.v_fps.get())
            bpm = float(self.v_bpm.get())
            if bpm <= 0: return
            s = float(self.v_start.get())
            e = float(self.v_end.get())
            p = int(self.v_prec.get())
        except ValueError:
            return

        curve = self.v_curve.get()
        length_str = self.v_length.get()
        if length_str == "1/8小節": fraction = 1/8
        elif length_str == "1/4小節": fraction = 1/4
        elif length_str == "1/2小節": fraction = 1/2
        else: fraction = 1.0

        measure_den = 0
        for n in range(1, 10000):
            x = (n * 240 * fps) / bpm
            if abs(x - round(x)) < 1e-6:
                measure_den = int(round(x))
                break
                
        if measure_den == 0:
            self.txt_after.delete("1.0", tk.END)
            self.txt_after.insert("1.0", "エラー: 適切なMEASUREが算出できません。")
            return

        lines_count = int(measure_den * fraction)
        if lines_count <= 0:
            lines_count = 1
        
        out = []
        out.append(f"// --- ストロボ開始 (BPM{bpm:g}, {fps}fps) ---")
        out.append(f"#MEASURE 1/{measure_den}")
        
        for i in range(lines_count):
            t = i / (lines_count - 1) if lines_count > 1 else 0.0
            y = self._get_curve_val(t, curve)
            val = f"{s + (e-s)*y:.{p}f}"
            out.append(f"#SCROLL {val}")
            out.append("0,")
            
        out.append("// --- ストロボ終了 ---")
        out.append("#MEASURE 4/4")
        out.append("#SCROLL 1.000")

        self.txt_after.delete("1.0", tk.END)
        self.txt_after.insert("1.0", "\n".join(out))

    def _apply(self):
        t = self.txt_after.get("1.0", tk.END).strip()
        if t and not t.startswith("エラー:"):
            self.apply_cb(t)
            self.destroy()

# ==============================================================================
#  ハイスピ変換ダイアログ
# ==============================================================================
class HighSpeedDialog(Toplevel):
    def __init__(self, editor_app, initial_text, apply_cb):
        super().__init__(editor_app.master)
        self.editor_app = editor_app
        self.title("ハイスピ変換")
        self.geometry("580x780")
        self.configure(bg=COLORS["bg"])
        self.apply_cb = apply_cb
        self.transient(editor_app.master)
        self.grab_set()
        self._build(initial_text)
        self._preview()

    def _label(self, parent, text):
        tk.Label(parent, text=text, bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9)).pack(anchor="w", padx=12, pady=(10, 2))

    def _build(self, text):
        self._label(self, "▸ 変換前の譜面データ（編集可）")
        self.txt_before = tk.Text(self, height=6, font=("Consolas", 11), bg=COLORS["surface"], fg=COLORS["fg"], insertbackground=COLORS["cursor"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["border"])
        self.txt_before.pack(fill="x", padx=12, pady=4)
        self.txt_before.insert("1.0", text)
        self.txt_before.bind("<KeyRelease>", self._preview)

        frm = tk.LabelFrame(self, text=" パラメータ ", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9), bd=1, relief="flat", highlightbackground=COLORS["border"], highlightthickness=1)
        frm.pack(fill="x", padx=12, pady=8)

        def row(r, label, var, is_combo=False, values=None, is_spin=False):
            tk.Label(frm, text=label, bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=r, column=0, sticky="e", padx=8, pady=6)
            if is_combo:
                w = ttk.Combobox(frm, textvariable=var, values=values, state="readonly", width=26)
            elif is_spin:
                w = tk.Spinbox(frm, from_=1, to=5, textvariable=var, width=6, bg=COLORS["surface"], fg=COLORS["fg"], buttonbackground=COLORS["surface"], highlightthickness=0)
            else:
                w = tk.Entry(frm, textvariable=var, width=14, bg=COLORS["surface"], fg=COLORS["fg"], insertbackground=COLORS["cursor"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["border"])
            w.grid(row=r, column=1, sticky="w", padx=8, pady=6)
            return w

        self.v_mode  = tk.StringVar(value="なめらかハイスピ")
        self.v_start = tk.StringVar(value="1.0")
        self.v_end   = tk.StringVar(value="2.0")
        self.v_curve = tk.StringVar(value="直線 (Linear)")
        self.v_prec  = tk.StringVar(value="2")
        self.v_interval = tk.StringVar(value="8")

        row(0, "変換モード", self.v_mode, is_combo=True, values=("なめらかハイスピ", "ノーツ毎ハイスピ", "特定間隔ハイスピ"))
        row(1, "開始 SCROLL", self.v_start)
        row(2, "終了 SCROLL", self.v_end)
        row(3, "変化カーブ", self.v_curve, is_combo=True, values=("直線 (Linear)", "徐々に加速 (Ease-In)", "徐々に減速 (Ease-Out)", "S字 (Ease-In-Out)"))
        row(4, "小数点以下", self.v_prec, is_spin=True)
        
        self.lbl_interval = tk.Label(frm, text="分割間隔(分音符)", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10))
        self.ent_interval = tk.Entry(frm, textvariable=self.v_interval, width=14, bg=COLORS["surface"], fg=COLORS["fg"], insertbackground=COLORS["cursor"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["border"])

        self._label(self, "▸ 変換後プレビュー")
        
        bf = tk.Frame(self, bg=COLORS["bg"])
        bf.pack(side=tk.BOTTOM, fill="x", pady=10)
        tk.Button(bf, text="キャンセル", command=self.destroy, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10), relief="flat", padx=14, pady=6).pack(side="right", padx=8)
        tk.Button(bf, text="エディタに適用", command=self._apply, bg=COLORS["accent"], fg=COLORS["fg_bright"], font=("Consolas", 10, "bold"), relief="flat", padx=14, pady=6).pack(side="right")

        self.txt_after = tk.Text(self, font=("Consolas", 11), bg=COLORS["bg2"], fg=COLORS["fg"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["accent"])
        self.txt_after.pack(side=tk.TOP, fill="both", expand=True, padx=12, pady=4)

        self.v_mode.trace_add("write", self._on_mode_change)
        for v in (self.v_start, self.v_end, self.v_curve, self.v_prec, self.v_interval):
            v.trace_add("write", self._preview)

        self._on_mode_change()

    def _on_mode_change(self, *_):
        mode = self.v_mode.get()
        if "特定間隔" in mode:
            self.lbl_interval.grid(row=5, column=0, sticky="e", padx=8, pady=6)
            self.ent_interval.grid(row=5, column=1, sticky="w", padx=8, pady=6)
        else:
            self.lbl_interval.grid_remove()
            self.ent_interval.grid_remove()
        self._preview()

    def _get_curve_val(self, t, curve_type):
        if "加速" in curve_type: return t * t
        elif "減速" in curve_type: return 1 - (1 - t) ** 2
        elif "S字" in curve_type: return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
        return t

    def _preview(self, *_):
        try:
            s = float(self.v_start.get())
            e = float(self.v_end.get())
            p = int(self.v_prec.get())
        except ValueError: return

        mode = self.v_mode.get()
        curve = self.v_curve.get()
        raw = self.txt_before.get("1.0", tk.END).strip()
        notes_str = "".join(c for c in raw if c in "0123456789,")

        if not notes_str: return
        out = []

        if "特定間隔" in mode:
            try: interval = int(self.v_interval.get())
            except ValueError: return
            try:
                out_str = self._apply_interval_highspeed(raw, interval, s, e, curve, p)
                self.txt_after.delete("1.0", tk.END)
                self.txt_after.insert("1.0", out_str)
            except Exception as ex:
                self.txt_after.delete("1.0", tk.END)
                self.txt_after.insert("1.0", f"エラー: {str(ex)}")
            return

        if "なめらか" in mode:
            note_list = [c for c in notes_str if c != ","]
            n = len(note_list)
            if n == 0:
                self.txt_after.delete("1.0", tk.END); self.txt_after.insert("1.0", notes_str)
                return
            ni = 0
            for c in notes_str:
                if c == ",":
                    if out: out[-1] += ","
                    continue
                t = ni / (n - 1) if n > 1 else 0.0
                y = self._get_curve_val(t, curve)
                val = f"{s + (e-s)*y:.{p}f}"
                out.append(f"#SCROLL {val}")
                out.append(c)
                ni += 1

        elif "ノーツ毎" in mode:
            # 8(連打終点)を除外するよう修正
            active_notes = [c for c in notes_str if c in "12345679"]
            n = len(active_notes)
            if n == 0:
                self.txt_after.delete("1.0", tk.END); self.txt_after.insert("1.0", notes_str)
                return
            ni = 0; buffer = ""
            for c in notes_str:
                # 8(連打終点)を除外するよう修正
                if c in "12345679":
                    if buffer: out.append(buffer); buffer = ""
                    t = ni / (n - 1) if n > 1 else 0.0
                    y = self._get_curve_val(t, curve)
                    val = f"{s + (e-s)*y:.{p}f}"
                    out.append(f"#SCROLL {val}")
                    buffer += c
                    ni += 1
                else: buffer += c
            if buffer: out.append(buffer)

        self.txt_after.delete("1.0", tk.END)
        self.txt_after.insert("1.0", "\n".join(out))

    def _apply(self):
        t = self.txt_after.get("1.0", tk.END).strip()
        if t and not t.startswith("エラー:"):
            self.apply_cb(t)
            self.destroy()

    def _apply_interval_highspeed(self, raw_text, interval, s, e, curve, p):
        if "," not in raw_text: raise ValueError("小節の終端（カンマ）が含まれていません。1小節以上を選択してください。")
        measures = raw_text.split(",")
        out = []
        for i, m_str in enumerate(measures):
            if i == len(measures) - 1 and not m_str.strip(): continue
            notes = "".join(c for c in m_str if c in "0123456789")
            length = len(notes)
            if length == 0:
                out.append(m_str + ",")
                continue
            chunk_size = max(1, length // interval)
            chunks = [notes[j:j+chunk_size] for j in range(0, length, chunk_size)]
            n = len(chunks)
            m_out = []
            for j, chunk in enumerate(chunks):
                t = j / (n - 1) if n > 1 else 0.0
                y = self._get_curve_val(t, curve)
                val = f"{s + (e-s)*y:.{p}f}"
                m_out.append(f"#SCROLL {val}")
                m_out.append(chunk)
            out.append("\n".join(m_out) + ",")
        return "\n".join(out)

# ==============================================================================
#  ノーツ間隔リサイズダイアログ
# ==============================================================================
class MeasureConvertDialog(Toplevel):
    BASE   = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 48, 64, 96]
    EXTEND = [128, 192, 256, 384, 512, 768, 1024]

    def __init__(self, editor_app, initial_text, apply_cb):
        super().__init__(editor_app.master)
        self.editor_app = editor_app
        self.title("ノーツ間隔リサイズ")
        self.geometry("580x680")
        self.configure(bg=COLORS["bg"])
        self.apply_cb = apply_cb
        self.transient(editor_app.master)
        self.grab_set()
        self.parsed = self._parse(initial_text)
        self._build(initial_text)
        self._update_targets()
        self._preview()

    def _parse(self, text):
        result = []
        for line in text.split('\n'):
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith('//'):
                result.append({"type": "keep", "text": line})
                continue
            parts = line.split('//', 1)
            code    = parts[0]
            comment = "//" + parts[1] if len(parts) > 1 else ""
            notes   = "".join(c for c in code if c in "0123456789")
            if notes:
                ls = code[:len(code) - len(code.lstrip())]
                result.append({"type": "notes", "original": line, "notes": notes, "has_comma": ',' in code, "leading_space": ls, "comment": comment})
            else:
                result.append({"type": "keep", "text": line})
        return result

    def _min_len(self, notes):
        n = len(notes)
        if n <= 1: return max(1, n)
        for d in range(1, n + 1):
            if n % d == 0:
                k = n // d
                ok = True
                for i in range(d):
                    if not all(notes[i*k+j] == '0' for j in range(1, k)):
                        ok = False; break
                if ok: return d
        return n

    def _build(self, text):
        tk.Label(self, text="▸ 変換前", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9)).pack(anchor="w", padx=12, pady=(10, 2))
        tb = tk.Text(self, height=6, font=("Consolas", 11), bg=COLORS["surface"], fg=COLORS["fg"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["border"], state="disabled")
        tb.pack(fill="x", padx=12, pady=4)
        tb.config(state="normal"); tb.insert("1.0", text); tb.config(state="disabled")

        frm = tk.LabelFrame(self, text=" 設定 ", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9), bd=1, relief="flat", highlightbackground=COLORS["border"], highlightthickness=1)
        frm.pack(fill="x", padx=12, pady=8)

        tk.Label(frm, text="変換後の桁数", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=0, column=0, sticky="e", padx=8, pady=8)
        self.v_target = tk.StringVar()
        self.cb_target = ttk.Combobox(frm, textvariable=self.v_target, state="readonly", width=14)
        self.cb_target.grid(row=0, column=1, sticky="w", padx=8)

        tk.Label(frm, text="折り返し文字数", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=1, column=0, sticky="e", padx=8, pady=8)
        self.v_wrap = tk.StringVar()
        self.cb_wrap = ttk.Combobox(frm, textvariable=self.v_wrap, state="readonly", width=14)
        self.cb_wrap.grid(row=1, column=1, sticky="w", padx=8)

        self.v_target.trace_add("write", self._update_wrap_options)
        self.v_wrap.trace_add("write", self._preview)

        tk.Label(self, text="▸ 変換後プレビュー", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9)).pack(anchor="w", padx=12, pady=(10, 2))
        
        bf = tk.Frame(self, bg=COLORS["bg"])
        bf.pack(side=tk.BOTTOM, fill="x", pady=10)
        tk.Button(bf, text="キャンセル", command=self.destroy, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10), relief="flat", padx=14, pady=6).pack(side="right", padx=8)
        tk.Button(bf, text="エディタに適用", command=self._apply, bg=COLORS["accent"], fg=COLORS["fg_bright"], font=("Consolas", 10, "bold"), relief="flat", padx=14, pady=6).pack(side="right")

        self.txt_after = tk.Text(self, font=("Consolas", 11), bg=COLORS["bg2"], fg=COLORS["fg"], borderwidth=0, highlightthickness=1, highlightbackground=COLORS["accent"])
        self.txt_after.pack(side=tk.TOP, fill="both", expand=True, padx=12, pady=4)

    def _update_targets(self):
        use_ext = self.editor_app.config_data.get("resize_ext", False)
        pool = self.BASE + (self.EXTEND if use_ext else [])
        base = 1
        has_notes = False
        for p in self.parsed:
            if p["type"] == "notes":
                m = self._min_len(p["notes"])
                if m > 0:
                    base = base * m // math.gcd(base, m)
                    has_notes = True
        valid = [str(t) for t in pool if (t % base == 0)] if has_notes else [str(t) for t in pool]
        self.cb_target["values"] = valid or ["変換不可"]
        if self.v_target.get() not in valid: self.v_target.set(valid[0] if valid else "変換不可")

    def _update_wrap_options(self, *_):
        try: tgt = int(self.v_target.get())
        except ValueError: return
        cfg = self.editor_app.config_data
        
        # 48等の公倍数を正しく処理するため、12の倍数判定を優先する
        if tgt % 12 == 0:
            vals = ["12", "24", "48", "改行なし"]
            dflt = str(cfg.get("resize_wrap_12", 24))
        elif tgt % 16 == 0:
            vals = ["16", "32", "64", "改行なし"]
            dflt = str(cfg.get("resize_wrap_16", 16))
        else:
            vals = ["改行なし"]
            dflt = "改行なし"
            
        self.cb_wrap["values"] = vals
        if self.v_wrap.get() not in vals: self.v_wrap.set(dflt if dflt in vals else vals[0])
        self._preview()

    def _convert(self, notes, target):
        if not notes: return ""
        m  = self._min_len(notes)
        k1 = len(notes) // m
        shrunk = "".join(notes[i * k1] for i in range(m))
        k2 = target // m
        return "".join(c + "0" * (k2 - 1) for c in shrunk)

    def _preview(self, *_):
        try: tgt = int(self.v_target.get())
        except ValueError: return
        wrap_val = 0
        if self.v_wrap.get() != "改行なし":
            try: wrap_val = int(self.v_wrap.get())
            except ValueError: pass

        out = []
        for p in self.parsed:
            if p["type"] == "keep":
                out.append(p["text"])
            else:
                converted = self._convert(p["notes"], tgt)
                if wrap_val > 0 and len(converted) > wrap_val:
                    chunks = [converted[i:i+wrap_val] for i in range(0, len(converted), wrap_val)]
                    for i, chunk in enumerate(chunks):
                        is_last = (i == len(chunks) - 1)
                        line = p["leading_space"] if i == 0 else ""
                        line += chunk
                        if is_last and p["has_comma"]: line += ","
                        if is_last and p["comment"]: line += " " + p["comment"]
                        out.append(line)
                else:
                    line = p["leading_space"] + converted + ("," if p["has_comma"] else "")
                    if p["comment"]: line += " " + p["comment"]
                    out.append(line)
        self.txt_after.delete("1.0", tk.END)
        self.txt_after.insert("1.0", "\n".join(out))

    def _apply(self):
        t = self.txt_after.get("1.0", tk.END).strip()
        if t:
            self.apply_cb(t)
            self.destroy()

# ==============================================================================
#  譜面解析
# ==============================================================================
class TJACourseAnalyzer:
    DIFF = {"0": "Easy", "Easy": "Easy", "1": "Normal", "Normal": "Normal", "2": "Hard", "Hard": "Hard", "3": "Oni", "Oni": "Oni", "4": "Edit", "Edit": "Edit"}
    DIFF_LABEL = {"Easy": "かんたん", "Normal": "ふつう", "Hard": "むずかしい", "Oni": "おに", "Edit": "おに(裏)"}
    DIFF_COLOR = {"Easy": "#55efc4", "Normal": "#74b9ff", "Hard": "#ffeaa7", "Oni": "#ff7675", "Edit": "#a29bfe"}

    def __init__(self, editor_app):
        self.editor_app = editor_app

    def parse_courses(self, content):
        lines  = content.split('\n')
        result = []
        buffer = []
        cname  = "Oni"
        in_score = False
        
        for line in lines:
            s = line.strip()
            if s.startswith("COURSE:"):
                if buffer: result.append({"key": cname, "data": buffer})
                cname = self.DIFF.get(s[7:].strip(), "Oni")
                buffer = []; in_score = False
            elif s.startswith("#START"):
                in_score = True; buffer.append(s)
            elif s.startswith("#END"):
                buffer.append(s); in_score = False
            elif in_score:
                buffer.append(s)
        if buffer: result.append({"key": cname, "data": buffer})

        out = []
        for c in result:
            stats = self._analyze(c["data"], lines)
            # ▼ ここに "data": c["data"] を追加して、辞書にデータを保持させます
            out.append({
                "key": c["key"], 
                "label": self.DIFF_LABEL.get(c["key"], c["key"]), 
                "color": self.DIFF_COLOR.get(c["key"], COLORS["fg"]), 
                "data": c["data"], 
                **stats
            })
        return out

    def _analyze(self, data, all_lines):
        bpm = Decimal("120")
        balloon_defs = []

        for l in all_lines:
            if l.startswith("BPM:"):
                try: bpm = Decimal(l[4:].strip())
                except: pass
            elif l.startswith("BALLOON:"):
                balloon_defs = [int(x.strip()) for x in l[8:].split(',') if x.strip().isdigit()]

        events = []
        for line in data:
            line = line.split("//")[0].strip()
            if not line: continue
            if line.startswith("#"):
                if line.startswith("#BPMCHANGE"):
                    try: events.append(("#BPMCHANGE", Decimal(line.split()[1])))
                    except: pass
                elif line.startswith("#MEASURE"):
                    m = re.search(r"(\d+)/(\d+)", line)
                    if m: events.append(("#MEASURE", Decimal(m.group(1))/Decimal(m.group(2))))
                elif line.startswith("#DELAY"):
                    try: events.append(("#DELAY", Decimal(line.split()[1])))
                    except: pass
                continue
            
            for c in line:
                if c in "0123456789": events.append(("NOTE", c))
                elif c == ",": events.append(("COMMA", None))

        measures = []
        cur_m = []
        for ev in events:
            if ev[0] == "COMMA":
                measures.append(cur_m)
                cur_m = []
            else:
                cur_m.append(ev)
        if cur_m:
            measures.append(cur_m)

        total_time = Decimal("0")
        curr_bpm = bpm
        measure_val = Decimal("1")
        
        don = 0; ka = 0; big_don = 0; big_ka = 0
        roll_start_time = None
        balloon_start_time = None
        b_idx = 0
        
        rolls_info = []
        balloons_info = []

        for m_events in measures:
            n_len = sum(1 for ev in m_events if ev[0] == "NOTE")
            for ev in m_events:
                if ev[0] == "#BPMCHANGE": curr_bpm = ev[1]
                elif ev[0] == "#MEASURE": measure_val = ev[1]
                elif ev[0] == "#DELAY": total_time += ev[1]
                elif ev[0] == "NOTE":
                    time_per_note = (Decimal("240") * measure_val / curr_bpm) / n_len if n_len > 0 else Decimal("0")
                    c = ev[1]
                    if c == "1": don += 1
                    elif c == "2": ka += 1
                    elif c == "3": big_don += 1
                    elif c == "4": big_ka += 1
                    elif c in "56":
                        roll_start_time = total_time
                    elif c == "7":
                        balloon_start_time = total_time
                        hits = balloon_defs[b_idx] if b_idx < len(balloon_defs) else 0
                        balloons_info.append({"duration": 0.0, "hits": hits})
                        b_idx += 1
                    elif c == "8":
                        if roll_start_time is not None:
                            dur = float(total_time - roll_start_time)
                            short_roll = self.editor_app.config_data.get("short_roll_comp", "段階的補正 (60fps理論値)")
                            rs = self.editor_app.config_data.get("roll_speed", 45)
                            
                            if short_roll == "段階的補正 (60fps理論値)":
                                if dur <= 0.10: hits = int(dur * max(60, rs))
                                elif dur <= 0.15: hits = int(dur * max(55, rs))
                                else: hits = int(dur * rs)
                            elif short_roll == "段階的補正 (理論値-1)":
                                if dur <= 0.10: hits = int(dur * max(55, rs))
                                elif dur <= 0.15: hits = int(dur * max(50, rs))
                                else: hits = int(dur * rs)
                            else:
                                hits = int(dur * rs)
                                
                            rolls_info.append({"duration": dur, "hits": hits})
                            roll_start_time = None
                        elif balloon_start_time is not None:
                            dur = float(total_time - balloon_start_time)
                            if balloons_info:
                                balloons_info[-1]["duration"] = dur
                            balloon_start_time = None
                    total_time += time_per_note

        f = float(total_time)
        m, s = divmod(int(f), 60)
        ms = int((total_time - int(total_time)) * 1000)
        time_str = f"{m}:{s:02}.{ms:03}"

        return {
            "notes": don + ka + big_don + big_ka,
            "measures": len(measures),
            "time": time_str,
            "rolls_info": rolls_info,
            "balloons_info": balloons_info
        }

# ==============================================================================
#  譜面画像プレビュー＆保存ダイアログ (完全タイムライン連動版)
# ==============================================================================
class TJAImagePreviewDialog(Toplevel):
    def __init__(self, editor_app, content, selected_label):
        super().__init__(editor_app.master)
        self.editor_app = editor_app
        self.title("譜面画像プレビュー")
        self.geometry("1000x700")
        self.configure(bg=COLORS["bg"])
        self.transient(editor_app.master)
        self.grab_set()
        
        self.content = content
        self.selected_label = selected_label
        self.img = None
        self.tk_img = None
        self.scale = 1.0
        self.sprites = {}
        
        if not HAS_PIL:
            tk.Label(self, text="画像生成には Pillow ライブラリが必要です。", bg=COLORS["bg"], fg=COLORS["err"], font=("Consolas", 10)).pack(pady=30)
            return

        self._load_sprites()
        self._build_ui()
        self.after(100, self._generate_and_show)

    def _load_sprites(self):
        if os.path.exists("notes.png"):
            try:
                sheet = Image.open("notes.png").convert("RGBA")
                sw, sh = sheet.size
                row_h = sh // 2
                u = sw / 13.4 
                self.sprites['1'] = sheet.crop((0, 0, int(u), row_h))
                self.sprites['2'] = sheet.crop((int(u), 0, int(2*u), row_h))
                self.sprites['3'] = sheet.crop((int(2*u), 0, int(3.2*u), row_h))
                self.sprites['4'] = sheet.crop((int(3.2*u), 0, int(4.4*u), row_h))
                self.sprites['5_head'] = sheet.crop((int(4.4*u), 0, int(5.4*u), row_h))
                self.sprites['6_head'] = sheet.crop((int(7.4*u), 0, int(8.4*u), row_h))
                self.sprites['7'] = sheet.crop((int(10.4*u), 0, int(11.9*u), row_h))
                self.sprites['9'] = sheet.crop((int(11.9*u), 0, sw, row_h))
            except Exception as e:
                print(f"画像読み込みエラー: {e}")

    def _build_ui(self):
        self.lbl_status = tk.Label(self, text="画像生成中...", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 12))
        self.lbl_status.pack(pady=20)
        
        self.canvas_frame = tk.Frame(self, bg=COLORS["bg2"])
        self.canvas = tk.Canvas(self.canvas_frame, bg=COLORS["bg2"], highlightthickness=0)
        self.vbar = tk.Scrollbar(self.canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.hbar = tk.Scrollbar(self.canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.hbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        
        self.btn_frame = tk.Frame(self, bg=COLORS["bg"])
        tk.Button(self.btn_frame, text="キャンセル", command=self.destroy, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10, "bold"), relief="flat", padx=14, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=10)
        self.btn_save = tk.Button(self.btn_frame, text="この画像を保存", command=self._save_image, bg=COLORS["accent"], fg=COLORS["fg_bright"], font=("Consolas", 10, "bold"), relief="flat", padx=16, pady=6, cursor="hand2", state=tk.DISABLED)
        self.btn_save.pack(side=tk.RIGHT, padx=10)

    def _on_mousewheel(self, event):
        if not self.img: return
        delta = 1 if (event.num == 4 or event.delta > 0) else -1
        self.scale *= (1.2 if delta > 0 else 0.8)
        self.scale = max(0.1, min(self.scale, 5.0))
        self._redraw_image()

    def _on_drag_start(self, event): self.canvas.scan_mark(event.x, event.y)
    def _on_drag_motion(self, event): self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _redraw_image(self):
        if not self.img: return
        new_w, new_h = int(self.img.width * self.scale), int(self.img.height * self.scale)
        if new_w <= 0 or new_h <= 0: return
        try: resample_filter = Image.Resampling.NEAREST
        except AttributeError: resample_filter = Image.NEAREST
        resized = self.img.resize((new_w, new_h), resample_filter)
        self.tk_img = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        self.canvas.configure(scrollregion=self.canvas.bbox(tk.ALL))

    def _get_font(self, size, bold=True):
        fonts = ["meiryob.ttc", "msgothic.ttc", "YuGothB.ttc", "Arialbd.ttf"] if bold else ["meiryo.ttc", "msgothic.ttc", "YuGothM.ttc", "Arial.ttf"]
        for f in fonts:
            try: return ImageFont.truetype(f, size)
            except IOError: pass
        return ImageFont.load_default()

    def _generate_and_show(self):
        try:
            self._generate_image()
            if self.img:
                self.lbl_status.pack_forget()
                self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
                self.btn_frame.pack(fill=tk.X, pady=10)
                self.update_idletasks()
                canvas_w = self.canvas.winfo_width()
                if canvas_w < 100: canvas_w = 880
                self.scale = (canvas_w / self.img.width) if self.img.width > canvas_w else 1.0
                self._redraw_image()
                self.btn_save.config(state=tk.NORMAL)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.lbl_status.config(text=f"エラーが発生しました: {str(e)}", fg=COLORS["err"])

    def _generate_image(self):
        courses = self.editor_app.analyzer.parse_courses(self.content)
        target_course = next((c for c in courses if c["label"] == self.selected_label), None)
        if target_course is None: raise ValueError("コースが見つかりません。")
        
        level, balloon_defs = 0, []
        for l in self.content.split('\n'):
            if l.startswith("LEVEL:"): 
                try: level = int(l[6:].strip())
                except: pass
            if l.startswith("BALLOON:"):
                balloon_defs = [int(x.strip()) for x in l[8:].split(',') if x.strip().isdigit()]

        gogo_active = False
        raw_measures, cur_m_lines = [], []
        for line in target_course["data"]:
            line = line.split("//")[0].strip()
            if not line: continue
            if line.startswith("#"): cur_m_lines.append(('cmd', line))
            else:
                parts = line.split(',')
                for i, part in enumerate(parts):
                    if part: cur_m_lines.append(('notes', part))
                    if i < len(parts) - 1:
                        raw_measures.append(cur_m_lines); cur_m_lines = []
        if cur_m_lines: raw_measures.append(cur_m_lines)

        # -------------------------------------------------------------
        # 1. タイムライン解析 (小節を4分音符を1とする「拍(beat)」単位に変換)
        # -------------------------------------------------------------
        parsed_measures = []
        current_num, current_den = 4, 4
        total_beats = 0.0

        for m_idx, rm in enumerate(raw_measures):
            total_notes = sum(len(val) for type_, val in rm if type_ == 'notes')
            note_idx = 0
            
            m_data = {
                'idx': m_idx,
                'notes': [], 
                'commands': [], 
                'gogo_start': gogo_active, 
                'gogo_toggles': []
            }
            
            for type_, val in rm:
                frac = note_idx / total_notes if total_notes > 0 else 0.0
                if type_ == 'cmd':
                    if val.startswith("#MEASURE"):
                        m = re.search(r"(\d+)/(\d+)", val)
                        if m: current_num, current_den = int(m.group(1)), int(m.group(2))
                    elif val.startswith("#GOGOSTART"):
                        gogo_active = True
                        m_data['gogo_toggles'].append((frac, True))
                    elif val.startswith("#GOGOEND"):
                        gogo_active = False
                        m_data['gogo_toggles'].append((frac, False))
                    elif val.startswith("#BPMCHANGE"):
                        try: m_data['commands'].append((frac, f"BPM {float(val.split()[1]):g}", "#0088ff"))
                        except: pass
                    elif val.startswith("#SCROLL"):
                        try: m_data['commands'].append((frac, f"HS {float(val.split()[1]):g}", "#ff0000"))
                        except: pass
                elif type_ == 'notes':
                    for ch in val:
                        frac = note_idx / total_notes if total_notes > 0 else 0.0
                        if ch in "123456789": m_data['notes'].append((frac, ch))
                        if ch in "0123456789": note_idx += 1
                            
            m_data['num'] = current_num
            m_data['den'] = current_den
            m_data['length_beats'] = current_num * 4 / current_den
            m_data['start_beat'] = total_beats
            total_beats += m_data['length_beats']
            
            parsed_measures.append(m_data)

        # -------------------------------------------------------------
        # 2. イベントの絶対座標(beat)化
        # -------------------------------------------------------------
        events_cmd = []
        events_note = []
        events_roll = []
        active_roll = None

        for m in parsed_measures:
            m_start = m['start_beat']
            m_len = m['length_beats']
            
            for frac, cmd_str, color in m['commands']:
                b = m_start + frac * m_len
                events_cmd.append({'beat': b, 'text': cmd_str, 'color': color})
                
            for frac, c in m['notes']:
                b = m_start + frac * m_len
                if c in '567':
                    active_roll = {'r_type': c, 'start_beat': b}
                    events_note.append({'beat': b, 'note': c})
                elif c == '8' and active_roll:
                    events_roll.append({'r_type': active_roll['r_type'], 'start_beat': active_roll['start_beat'], 'end_beat': b})
                    active_roll = None
                elif c in '12349':
                    events_note.append({'beat': b, 'note': c})
                    
        if active_roll:
            events_roll.append({'r_type': active_roll['r_type'], 'start_beat': active_roll['start_beat'], 'end_beat': total_beats})

        # ゴーゴータイム区間の抽出
        gogo_regions = []
        current_gogo = None
        for m in parsed_measures:
            if m['gogo_start'] and current_gogo is None: current_gogo = m['start_beat']
            for frac, state in m['gogo_toggles']:
                b = m['start_beat'] + frac * m['length_beats']
                if state and current_gogo is None: current_gogo = b
                elif not state and current_gogo is not None:
                    gogo_regions.append((current_gogo, b))
                    current_gogo = None
        if current_gogo is not None:
            gogo_regions.append((current_gogo, total_beats))

        # 風船の打数を割り当て
        balloon_idx = 0
        for n in events_note:
            if n['note'] == '7':
                n['hits'] = balloon_defs[balloon_idx] if balloon_idx < len(balloon_defs) else 0
                balloon_idx += 1

        # 左から重ねるため、時間を反転
        events_note.sort(key=lambda n: n['beat'], reverse=True)

        # -------------------------------------------------------------
        # 3. 描画設定と画像生成
        # -------------------------------------------------------------
        BEAT_WIDTH = 340 / 4   # 4分音符1つあたりのピクセル幅 (デフォルト340pxの4/4小節を基準)
        ROW_BEATS = 16.0       # 1行に入る最大拍数 (4/4小節が4つ分 = 16拍)
        LANE_HEIGHT = 46
        ROW_SPACING = 130
        MARGIN_X = 60
        MARGIN_Y = 170
        GOGO_BAND_HEIGHT = 20
        
        total_rows = int((total_beats - 1e-6) // ROW_BEATS) + 1 if total_beats > 0 else 1
        img_width = MARGIN_X * 2 + int(ROW_BEATS * BEAT_WIDTH)
        img_height = MARGIN_Y + total_rows * ROW_SPACING
        
        self.img = Image.new("RGB", (img_width, img_height), "#dbdbdb")
        draw = ImageDraw.Draw(self.img)
        
        font_title = self._get_font(40, bold=True)
        font_diff = self._get_font(24, bold=True)
        font_cmd = self._get_font(12, bold=True)
        font_num = self._get_font(13, bold=True)
        font_balloon = self._get_font(12, bold=True)
        
        title = next((l[6:].strip() for l in self.content.split('\n') if l.startswith("TITLE:")), "No Title")
        draw.text((MARGIN_X, 20), title, fill="#000000", font=font_title)
        draw.text((MARGIN_X, 65), f"{self.selected_label} {'★' * level}", fill=target_course["color"], font=font_diff)

        # -------------------------------------------------------------
        # 4. 描画ユーティリティ
        # -------------------------------------------------------------
        def get_row_x(beat):
            r = int(beat // ROW_BEATS)
            rem = beat - r * ROW_BEATS
            return r, MARGIN_X + rem * BEAT_WIDTH

        def draw_band(start_b, end_b, draw_func):
            # 行をまたぐ帯の描画を自動分割
            s_row = int(start_b // ROW_BEATS)
            e_row = int((end_b - 1e-6) // ROW_BEATS)
            if e_row < s_row: e_row = s_row
            
            for r in range(s_row, e_row + 1):
                row_start_b = max(start_b, r * ROW_BEATS)
                row_end_b = min(end_b, (r + 1) * ROW_BEATS)
                
                sx = MARGIN_X + (row_start_b - r * ROW_BEATS) * BEAT_WIDTH
                ex = MARGIN_X + (row_end_b - r * ROW_BEATS) * BEAT_WIDTH
                ly = MARGIN_Y + r * ROW_SPACING
                
                draw_func(r, sx, ex, ly, row_start_b == start_b, row_end_b == end_b)

        # --- A. 背景とレーン ---
        for r in range(total_rows):
            ly = MARGIN_Y + r * ROW_SPACING
            draw.rectangle([0, ly, img_width, ly + LANE_HEIGHT], fill="#757575")
            draw.line([0, ly + LANE_HEIGHT/2, img_width, ly + LANE_HEIGHT/2], fill="#cccccc", width=1)

        # --- B. ゴーゴータイム ---
        def draw_gogo(r, sx, ex, ly, is_first, is_last):
            if not is_first and sx <= MARGIN_X + 1e-6: sx = 0
            if not is_last and ex >= MARGIN_X + ROW_BEATS * BEAT_WIDTH - 1e-6: ex = img_width
            draw.rectangle([sx, ly - GOGO_BAND_HEIGHT, ex, ly], fill="#ffc2c2")
            
        for gb_start, gb_end in gogo_regions:
            draw_band(gb_start, gb_end, draw_gogo)

        # --- C. グリッド線 ---
        for m in parsed_measures:
            num, den = m['num'], m['den']
            div = (num * 2) if den % 4 == 0 else den
            step = m['length_beats'] / div
            for i in range(1, div):
                b = m['start_beat'] + i * step
                r, x = get_row_x(b)
                ly = MARGIN_Y + r * ROW_SPACING
                color = "#aaaaaa" if (den % 4 == 0 and i % 2 != 0) else "#888888"
                draw.line([x, ly, x, ly + LANE_HEIGHT], fill=color, width=1)

        # --- D. コマンド線とテキスト (階段状) ---
        row_cmds = {r: [] for r in range(total_rows + 1)}
        for cmd in events_cmd:
            r, x = get_row_x(cmd['beat'])
            row_cmds[r].append((x, cmd['text'], cmd['color']))
            
        for r in range(total_rows):
            cmds = sorted(row_cmds[r], key=lambda c: c[0])
            last_end_x = [0, 0, 0]
            ly = MARGIN_Y + r * ROW_SPACING
            for x, text, color in cmds:
                try: tw = draw.textlength(text, font=font_cmd)
                except: tw = len(text) * 7
                
                level = 0
                if x < last_end_x[0] + 8:
                    level = 1
                    if x < last_end_x[1] + 8: level = 2
                
                last_end_x[level] = x + 2 + tw
                cmd_y = ly - 36 - level * 14
                
                draw.line([x, cmd_y + 6, x, ly + LANE_HEIGHT], fill="#333333", width=2)
                draw.text((x + 2, cmd_y), text, fill=color, font=font_cmd)

        # --- E. 小節線（白太線） ---
        for m in parsed_measures:
            b = m['start_beat']
            r, x = get_row_x(b)
            ly = MARGIN_Y + r * ROW_SPACING
            
            draw.line([x, ly, x, ly + LANE_HEIGHT], fill="#ffffff", width=3)
            draw.text((x + 4, ly - 18), str(m['idx'] + 1), fill="#000000", font=font_num)
            
            # 行の右端にぴったり小節線が重なる場合の描画
            end_b = m['start_beat'] + m['length_beats']
            if end_b > 0 and end_b < total_beats and abs(end_b % ROW_BEATS) < 1e-6:
                r_end = int((end_b - 1e-6) // ROW_BEATS)
                x_end = MARGIN_X + ROW_BEATS * BEAT_WIDTH
                ly_end = MARGIN_Y + r_end * ROW_SPACING
                draw.line([x_end, ly_end, x_end, ly_end + LANE_HEIGHT], fill="#ffffff", width=3)

        # 曲の最後の小節線
        if total_beats > 0:
            r, x = get_row_x(total_beats)
            if abs(total_beats % ROW_BEATS) < 1e-6:
                r = int((total_beats - 1e-6) // ROW_BEATS)
                x = MARGIN_X + ROW_BEATS * BEAT_WIDTH
            ly = MARGIN_Y + r * ROW_SPACING
            draw.line([x, ly, x, ly + LANE_HEIGHT], fill="#ffffff", width=3)

        # --- F. 連打帯 ---
        def draw_roll(r, sx, ex, ly, is_first, is_last, color, thick):
            cy = ly + LANE_HEIGHT / 2
            draw.line([sx, cy, ex, cy], fill=color, width=thick)
            if is_first: draw.ellipse([sx - thick/2, cy - thick/2, sx + thick/2, cy + thick/2], fill=color)
            if is_last: draw.ellipse([ex - thick/2, cy - thick/2, ex + thick/2, cy + thick/2], fill=color)
            draw.line([sx, cy - thick/2, ex, cy - thick/2], fill="#000000", width=1)
            draw.line([sx, cy + thick/2, ex, cy + thick/2], fill="#000000", width=1)

        for roll in events_roll:
            color = "#fcdb38" if roll['r_type'] in '56' else "#ffb74d"
            thick = 34 if roll['r_type'] == '6' else 24
            draw_band(roll['start_beat'], roll['end_beat'], lambda r, sx, ex, ly, first, last: draw_roll(r, sx, ex, ly, first, last, color, thick))

        # --- G. ノーツ（左から重なる） ---
        for n in events_note:
            r, x = get_row_x(n['beat'])
            y = MARGIN_Y + r * ROW_SPACING + LANE_HEIGHT / 2
            nt = n['note']
            r_sm, r_bg = 12, 17
            
            if nt in self.sprites or (nt in '56' and f"{nt}_head" in self.sprites):
                spr = self.sprites.get(f"{nt}_head" if nt in '56' else nt)
                target_w = (r_bg*2+4 if nt in '3469' else r_sm*2+4)
                if nt in '79': target_w = int(target_w * 1.5)
                spr = spr.resize((target_w, target_w if nt not in '79' else int(target_w*0.8)), Image.Resampling.LANCZOS)
                self.img.paste(spr, (int(x - spr.width/2), int(y - spr.height/2)), spr)
                if nt == '7' and n.get('hits', 0) > 0:
                    hits = n['hits']
                    draw.ellipse([x-10, y-10, x+10, y+10], fill="#ffffff", outline="#000000", width=1)
                    draw.text((x-(3 if hits < 10 else 6), y-7), str(hits), fill="#000000", font=font_balloon)
            else:
                if nt in '12345679':
                    fill_c = {"1":"#f44336","2":"#29b6f6","3":"#f44336","4":"#29b6f6","5":"#fcdb38","6":"#fcdb38","7":"#ff9800","9":"#9c27b0"}[nt]
                    r_radius = r_bg if nt in '3469' else r_sm
                    draw.ellipse([x-r_radius, y-r_radius, x+r_radius, y+r_radius], fill=fill_c, outline="#ffffff", width=2)
                    if nt == '7' and n.get('hits', 0) > 0:
                        hits = n['hits']
                        draw.ellipse([x-10, y-10, x+10, y+10], fill="#ffffff", outline="#000000", width=1)
                        draw.text((x-(3 if hits < 10 else 6), y-7), str(hits), fill="#000000", font=font_balloon)

    def _save_image(self):
        title = next((l[6:].strip() for l in self.content.split('\n') if l.startswith("TITLE:")), "No Title")
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG Image", "*.png")], initialfile=f"{title}_{self.selected_label}.png", parent=self)
        if path:
            self.img.save(path)
            messagebox.showinfo("成功", "譜面画像を保存しました。", parent=self)
            self.destroy()

# ==============================================================================
#  メインエディタ
# ==============================================================================
class NeoTJAEditor:
    def __init__(self, master):
        self.master = master

        self.config_data = {
            "run_config": {k: {"name": f"シミュレータ{k}", "path": ""} for k in ("F1", "F2", "F3")},
            "custom_shortcuts": {str(i): "" for i in range(10)},
            "theme": "dark",
            "font_family": "Consolas",
            "font_size": 12,
            "resize_ext": False,
            "resize_wrap_16": 16,
            "resize_wrap_12": 24,
            "roll_speed": 45,
            "short_roll_comp": "段階的補正 (60fps理論値)"
        }
        self._load_settings()
        
        global COLORS
        COLORS.update(THEMES.get(self.config_data.get("theme", "dark"), THEMES["dark"]))

        master.title(f"{APP_NAME}  v{VERSION}")
        master.geometry("1280x820")
        master.configure(bg=COLORS["bg"])

        self._init_styles()

        self.analyzer  = TJACourseAnalyzer(self)
        self.current_file  = None
        self._after_id     = None
        self.modified_lines  = set()
        self.checkpoints     = set()
        self.balloon_hits    = {}
        self.roll_hits       = {}
        self.invalid_lines   = {}
        self.global_warnings = []
        self.courses_info    = []
        self._last_course_key = ""
        self._sidebar_refs    = {}

        self.encoding_var = tk.StringVar(value="utf-8")

        self.font_family = self.config_data.get("font_family", "Consolas")
        self.font_size   = self.config_data.get("font_size", 12)
        self._update_font()

        self._build_gui()
        self._bind_events()

        self.master.update()
        self._apply_theme_colors()
        self._draw_ruler()
        self._update_line_numbers()
        self._update_status()
        self._force_update()

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        
        style.configure("TCombobox", 
                        fieldbackground=COLORS["surface"], 
                        background=COLORS["surface"], 
                        foreground=COLORS["fg"], 
                        arrowcolor=COLORS["fg"], 
                        bordercolor=COLORS["border"],
                        insertcolor=COLORS["cursor"])
        
        style.map("TCombobox", 
                  fieldbackground=[("readonly", COLORS["surface"]), ("active", COLORS["surface"])],
                  selectbackground=[("readonly", COLORS["select"])],
                  selectforeground=[("readonly", COLORS["fg_bright"])],
                  foreground=[("readonly", COLORS["fg"]), ("disabled", COLORS["fg_dim"])],
                  background=[("readonly", COLORS["surface"])])
                  
        style.configure("TNotebook", background=COLORS["bg2"], borderwidth=0)
        style.configure("TNotebook.Tab", background=COLORS["surface"], foreground=COLORS["fg"], padding=[10, 4])
        style.map("TNotebook.Tab", background=[("selected", COLORS["accent"])], foreground=[("selected", COLORS["fg_bright"])])

    def _update_font(self):
        self.mono_font = font.Font(family=self.font_family, size=self.font_size)
        self.mono_bold = font.Font(family=self.font_family, size=self.font_size, weight="bold")
        self.char_w    = self.mono_font.measure("0" * 1000) / 1000.0
        self.char_h    = self.mono_font.metrics("linespace")

    def _build_gui(self):
        self.toolbar_top = tk.Frame(self.master, bg=COLORS["bg2"], height=40)
        self.toolbar_top.pack(side=tk.TOP, fill=tk.X)
        self.toolbar_top.pack_propagate(False)

        self.toolbar_bottom = tk.Frame(self.master, bg=COLORS["bg2"], height=40)
        self.toolbar_bottom.pack(side=tk.TOP, fill=tk.X)
        self.toolbar_bottom.pack_propagate(False)

        self._create_toolbar()

        self.bottom_frame = tk.Frame(self.master, bg=COLORS["bg3"])
        self.bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.btn_theme = tk.Button(self.bottom_frame, text="テーマ切替", command=self.toggle_theme,
                                   bg=COLORS["bg3"], fg=COLORS["fg_dim"], activebackground=COLORS["border"],
                                   activeforeground=COLORS["fg"], font=("Consolas", 9), relief="flat", bd=0, cursor="hand2")
        self.btn_theme.pack(side=tk.LEFT, padx=4)

        self.status_bar = tk.Label(self.bottom_frame, text="", bd=0, anchor=tk.W, bg=COLORS["bg3"], fg=COLORS["fg_dim"], font=("Consolas", 9), padx=10, pady=4)
        self.status_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        pw = tk.PanedWindow(self.master, orient=tk.HORIZONTAL, sashwidth=3, bg=COLORS["border"], sashrelief=tk.FLAT)
        pw.pack(fill=tk.BOTH, expand=True)

        self.sb_outer = tk.Frame(pw, bg=COLORS["bg2"])
        pw.add(self.sb_outer, width=260, stretch="never")
        self._build_sidebar()

        ef = tk.Frame(pw, bg=COLORS["bg"])
        pw.add(ef, stretch="always")
        self._build_editor(ef)

    def _btn(self, parent, text, cmd, accent=False):
        bg = COLORS["accent"] if accent else COLORS["toolbar_btn"]
        fg = COLORS["fg_bright"] if accent else COLORS["fg"]
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=COLORS["toolbar_hover"], activeforeground=COLORS["fg_bright"], font=("Consolas", 10), relief="flat", padx=12, pady=6, cursor="hand2", bd=0)
        b.pack(side=tk.LEFT, padx=2, pady=4)
        return b

    def _create_toolbar(self):
        self._btn(self.toolbar_top, "新規", self.new_file)
        self._btn(self.toolbar_top, "開く", self.open_file)
        self._btn(self.toolbar_top, "フォルダを開く", self.open_folder)
        self._btn(self.toolbar_top, "保存", self.save_file, accent=True)
        self._btn(self.toolbar_top, "元に戻す", self.text_area.edit_undo if hasattr(self, 'text_area') else lambda: None)
        self._btn(self.toolbar_top, "やり直す", self.text_area.edit_redo if hasattr(self, 'text_area') else lambda: None)

        self._btn(self.toolbar_top, "譜面画像生成(試験的)", self.open_image_exporter) # ←追加
        self._btn(self.toolbar_top, "ヘルプ", lambda: HelpWindow(self.master))

        enc_lbl = tk.Label(self.toolbar_top, textvariable=self.encoding_var, bg=COLORS["bg3"], fg=COLORS["fg_dim"], font=("Consolas", 9), padx=8)
        enc_lbl.pack(side=tk.RIGHT, padx=8)

        self._btn(self.toolbar_bottom, "ハイスピ変換", self.open_scroll_splitter)
        self._btn(self.toolbar_bottom, "リサイズ", self.open_measure_converter)
        self._btn(self.toolbar_bottom, "反転", self.reverse_don_ka)
        self._btn(self.toolbar_bottom, "ストロボ生成", self.open_strobe_tool)

    def _build_sidebar(self):
        self._sb_title = tk.Label(self.sb_outer, text="", justify="left", bg=COLORS["bg2"], fg=COLORS["fg"], font=("Consolas", 9), wraplength=240, anchor="w", padx=8, pady=8)
        self._sb_title.pack(fill="x")
        
        spd_frm = tk.Frame(self.sb_outer, bg=COLORS["bg2"])
        spd_frm.pack(fill="x", padx=8, pady=4)
        tk.Label(spd_frm, text="連打秒速:", bg=COLORS["bg2"], fg=COLORS["fg_dim"], font=("Consolas", 9)).pack(side=tk.LEFT)
        self.v_roll_speed = tk.StringVar(value=str(self.config_data.get("roll_speed", 45)))
        sb_spd = tk.Spinbox(spd_frm, from_=1, to=100, textvariable=self.v_roll_speed, width=4, bg=COLORS["surface"], fg=COLORS["fg"], buttonbackground=COLORS["surface"], highlightthickness=0)
        sb_spd.pack(side=tk.LEFT, padx=4)
        
        def on_spd_change(*_):
            try:
                self.config_data["roll_speed"] = int(self.v_roll_speed.get())
                self._force_update()
            except ValueError: pass
        self.v_roll_speed.trace_add("write", on_spd_change)

        tk.Frame(self.sb_outer, height=1, bg=COLORS["border"]).pack(fill="x", pady=4)
        
        self._sb_canvas = tk.Canvas(self.sb_outer, bg=COLORS["bg2"], highlightthickness=0)
        sb = tk.Scrollbar(self.sb_outer, orient="vertical", command=self._sb_canvas.yview, bg=COLORS["bg2"])
        self._sb_frame = tk.Frame(self._sb_canvas, bg=COLORS["bg2"])
        self._sb_frame.bind("<Configure>", lambda e: self._sb_canvas.configure(scrollregion=self._sb_canvas.bbox("all")))
        self._sb_canvas_window = self._sb_canvas.create_window((0, 0), window=self._sb_frame, anchor="nw")
        self._sb_canvas.bind("<Configure>", lambda e: self._sb_canvas.itemconfig(self._sb_canvas_window, width=e.width))
        self._sb_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._sb_canvas.pack(fill=tk.BOTH, expand=True)

    def _build_editor(self, parent):
        self.ruler = tk.Canvas(parent, height=22, bg=COLORS["surface"], highlightthickness=0)
        self.ruler.pack(side=tk.TOP, fill=tk.X)
        tc = tk.Frame(parent, bg=COLORS["bg"])
        tc.pack(fill=tk.BOTH, expand=True)
        self.line_numbers = tk.Text(tc, width=7, padx=4, pady=0, takefocus=0, bd=0, highlightthickness=0, bg=COLORS["surface"], fg=COLORS["fg_dim"], state="disabled", font=self.mono_font, spacing1=0, spacing2=0, spacing3=0)
        self.line_numbers.pack(side=tk.LEFT, fill=tk.Y)
        tk.Frame(tc, width=1, bg=COLORS["border"]).pack(side=tk.LEFT, fill=tk.Y)
        vsb = tk.Scrollbar(tc, bg=COLORS["bg2"])
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb = tk.Scrollbar(tc, orient=tk.HORIZONTAL, bg=COLORS["bg2"])
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._vsb = vsb
        self._hsb = hsb
        self.text_area = tk.Text(tc, undo=True, wrap="none", padx=8, bd=0, highlightthickness=0, bg=COLORS["bg"], fg=COLORS["fg"], insertbackground=COLORS["cursor"], selectbackground=COLORS["select"], selectforeground=COLORS["fg_bright"], font=self.mono_font, spacing1=0, spacing2=0, spacing3=0, yscrollcommand=vsb.set, xscrollcommand=self._on_xscroll)
        self.text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.config(command=self._sync_y)
        hsb.config(command=self._sync_x)
        self._setup_tags()
        self._create_menu()
        for toolbar in (self.toolbar_top, self.toolbar_bottom):
            for w in toolbar.winfo_children():
                if isinstance(w, tk.Button):
                    txt = w.cget("text")
                    if "元に戻す" in txt: w.config(command=self.text_area.edit_undo)
                    elif "やり直す" in txt: w.config(command=self.text_area.edit_redo)
        cm = tk.Menu(self.master, tearoff=0, bg=COLORS["bg3"], fg=COLORS["fg"], activebackground=COLORS["accent"], activeforeground=COLORS["fg_bright"])
        cm.add_command(label="元に戻す", command=self.text_area.edit_undo)
        cm.add_command(label="やり直す", command=self.text_area.edit_redo)
        cm.add_separator()
        cm.add_command(label="切り取り", command=lambda: self.text_area.event_generate("<<Cut>>"))
        cm.add_command(label="コピー", command=lambda: self.text_area.event_generate("<<Copy>>"))
        cm.add_command(label="貼り付け", command=lambda: self.text_area.event_generate("<<Paste>>"))
        cm.add_separator()
        cm.add_command(label="あべこべ反転", command=self.reverse_don_ka)
        cm.add_command(label="全選択", command=lambda: self.text_area.tag_add("sel", "1.0", "end"))
        self._ctx_menu = cm
        self.tooltip = ToolTip(self.text_area)

    def _setup_tags(self):
        nb = self.mono_bold
        nf = self.mono_font
        ta = self.text_area
        ta.tag_configure("num_1", foreground=COLORS["don"], font=nf)
        ta.tag_configure("num_2", foreground=COLORS["ka"], font=nf)
        ta.tag_configure("num_3", foreground=COLORS["don"], font=nb)
        ta.tag_configure("num_4", foreground=COLORS["ka"], font=nb)
        ta.tag_configure("num_0", foreground=COLORS["zero"], font=nf)
        ta.tag_configure("num_9", foreground=COLORS["zero"], font=nf)
        ta.tag_configure("roll", foreground=COLORS["roll"], font=nf)
        ta.tag_configure("roll_big", foreground=COLORS["roll"], font=nb)
        ta.tag_configure("balloon_tag", foreground=COLORS["balloon"], font=nf)
        ta.tag_configure("cmd", foreground=COLORS["cmd"], font=nf)
        ta.tag_configure("header_key", foreground=COLORS["header_key"], font=nf)
        ta.tag_configure("header_val", foreground=COLORS["header_val"], font=nf)
        ta.tag_configure("comment", foreground=COLORS["comment"], font=nf)
        ta.tag_configure("warn", background=COLORS["warn"], foreground="#000000")
        
        self.line_numbers.tag_configure("dirty", background="#2d3000" if self.config_data.get("theme") == "dark" else "#fff9c4")
        self.line_numbers.tag_configure("invalid", foreground=COLORS["err"], font=("Consolas", 8, "bold"))
        self.line_numbers.tag_configure("cp", foreground=COLORS["checkpoint"])

    def _bind_events(self):
        ta = self.text_area
        ta.bind("<KeyRelease>", self._on_key_release)
        ta.bind("<Button-1>", self._on_cursor_move)
        ta.bind("<ButtonRelease-1>", self._on_cursor_move)
        ta.bind("<MouseWheel>", self._sync_wheel)
        ta.bind("<Button-3>", lambda e: self._ctx_menu.post(e.x_root, e.y_root))
        ta.bind("<<Paste>>", lambda e: self.master.after(10, self._force_update))
        ta.bind("<Key>", self._mark_dirty)
        ta.bind("<Control-MouseWheel>", self._zoom)
        ta.bind("<Motion>", self._on_motion)
        ta.bind("<Control-Up>", lambda e: self._jump_cp("Up"))
        ta.bind("<Control-Down>", lambda e: self._jump_cp("Down"))
        ta.bind("<Control-m>", lambda e: self.reverse_don_ka())
        self.line_numbers.bind("<MouseWheel>", self._sync_wheel)
        self.master.bind("<F1>", lambda e: self._run_app("F1"))
        self.master.bind("<F2>", lambda e: self._run_app("F2"))
        self.master.bind("<F3>", lambda e: self._run_app("F3"))
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        self._bind_shortcuts()

    def _bind_shortcuts(self):
        ta = self.text_area
        def ins_space(cmd):
            ta.insert(tk.INSERT, cmd + " ")
            self._force_update()
            return "break"
        def ins_nl(cmd):
            ta.insert(tk.INSERT, cmd + "\n")
            self._force_update()
            return "break"
        ta.bind("<Alt-b>", lambda e: ins_space("#BPMCHANGE"))
        ta.bind("<Alt-s>", lambda e: ins_space("#SCROLL"))
        ta.bind("<Alt-d>", lambda e: ins_space("#DELAY"))
        ta.bind("<Alt-r>", lambda e: ins_space("#BRANCHSTART"))
        ta.bind("<Alt-u>", lambda e: ins_space("#MEASURE"))
        ta.bind("<Alt-g>", lambda e: ins_nl("#GOGOSTART"))
        ta.bind("<Control-g>", lambda e: ins_nl("#GOGOSTART"))
        ta.bind("<Alt-o>", lambda e: ins_nl("#GOGOEND"))
        ta.bind("<Control-o>", lambda e: ins_nl("#GOGOEND"))
        ta.bind("<Alt-l>", lambda e: ins_nl("#BARLINEON"))
        ta.bind("<Alt-i>", lambda e: ins_nl("#BARLINEOFF"))
        ta.bind("<Control-s>", lambda e: self.save_file())
        ta.bind("<Control-a>", lambda e: ta.tag_add("sel", "1.0", "end"))
        ta.bind("<Control-t>", lambda e: ta.event_generate("<<Cut>>"))
        ta.bind("<Alt-p>", self._toggle_cp)
        ta.bind("<Alt-Up>", lambda e: self._jump_cp("Up"))
        ta.bind("<Alt-Down>", lambda e: self._jump_cp("Down"))
        for k in "1234567890":
            ta.bind(f"<Alt-Key-{k}>", lambda e, key=k: self._insert_custom(key))

    def _sync_y(self, *args):
        self.text_area.yview(*args)
        self.line_numbers.yview(*args)
        self._draw_ruler()

    def _sync_x(self, *args):
        self.text_area.xview(*args)
        self._hsb.set(*args)
        self._draw_ruler()

    def _on_xscroll(self, *args):
        self._hsb.set(*args)
        self._draw_ruler()

    def _sync_wheel(self, event):
        delta = int(-1 * (event.delta / 120))
        self.line_numbers.yview_scroll(delta, "units")
        self.text_area.yview_scroll(delta, "units")
        self._draw_ruler()
        return "break"

    def _draw_ruler(self, event=None):
        c = self.ruler
        c.delete("all")
        w = c.winfo_width()
        lnw = self.line_numbers.winfo_width() + 1
        vis = self.text_area.index("@0,0")
        bbox = self.text_area.bbox(vis)
        
        offset = lnw + 8
        if bbox:
            col = int(vis.split('.')[1])
            offset = lnw + bbox[0] - col * self.char_w
            
        c.create_rectangle(0, 0, w, 22, fill=COLORS["surface"], outline="")
        
        if self.char_w <= 0: return
        
        start_col = int(max(0, -offset // self.char_w)) - 5
        end_col = int((w - offset) // self.char_w) + 15
        
        for i in range(start_col, end_col):
            x = offset + i * self.char_w
            if x < lnw: continue
            if i >= 0 and i % 4 == 0:
                c.create_line(x, 12, x, 22, fill=COLORS["border"])
                c.create_text(x, 5, text=str(i), font=("Consolas", 7), fill=COLORS["fg_dim"])
            elif i >= 0:
                c.create_line(x, 18, x, 22, fill=COLORS["border"])
                
        try:
            cur = self.text_area.bbox(tk.INSERT)
            if cur:
                cx = lnw + cur[0]
                c.create_rectangle(cx, 14, cx + self.char_w, 22, fill=COLORS["cursor"], outline="")
        except Exception: pass

    def _update_line_numbers(self):
        total = int(self.text_area.index(tk.END).split('.')[0]) - 1
        ln = self.line_numbers
        ln.config(state="normal")
        ln.delete("1.0", tk.END)
        parts = []
        for i in range(1, total + 1):
            mark = ""
            if i in self.checkpoints: mark += "▶"
            if i in self.invalid_lines: mark += "!"
            parts.append(f"{i:>4}{mark}")
        ln.insert("1.0", "\n".join(parts))
        for li in self.modified_lines:
            if li <= total: ln.tag_add("dirty", f"{li}.0", f"{li}.end")
        for li in self.checkpoints:
            if li <= total: ln.tag_add("cp", f"{li}.0", f"{li}.end")
        pos = "1.0"
        while True:
            pos = ln.search("!", pos, stopindex=tk.END)
            if not pos: break
            ln.tag_add("invalid", pos, f"{pos}+1c")
            pos = f"{pos}+1c"
        ln.config(state="disabled")
        ln.yview_moveto(self.text_area.yview()[0])

    def highlight_syntax(self):
        ta = self.text_area
        clear_tags = ["num_1", "num_2", "num_3", "num_4", "num_0", "num_9", "roll", "roll_big", "balloon_tag", "cmd", "header_key", "header_val", "comment", "warn"]
        for t in clear_tags: ta.tag_remove(t, "1.0", tk.END)
        for t in ta.tag_names():
            if t.startswith("bi_") or t.startswith("ri_"): ta.tag_remove(t, "1.0", tk.END)
                
        content = ta.get("1.0", tk.END)
        lines   = content.split('\n')
        
        self.global_warnings = []
        self.invalid_lines   = {}
        
        self.balloon_hits    = {}
        self.roll_hits       = {}
        
        sc = content.count("#START")
        ec = content.count("#END")
        if sc != ec: self.global_warnings.append(f"⚠ START/END 不一致 ({sc}/{ec})")
        
        gs = content.count("#GOGOSTART")
        ge = content.count("#GOGOEND")
        if gs > ge: self.global_warnings.append(f"⚠ GOGOEND 不足 ({gs}/{ge})")

        score_ranges = []
        in_r = False
        sl   = 0
        current_course = "Oni"
        
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("COURSE:"):
                current_course = self.analyzer.DIFF.get(s[7:].strip(), "Oni")
            elif s.startswith("#START"):
                in_r = True; sl = i
            elif s.startswith("#END") and in_r:
                score_ranges.append((sl, i, current_course)); in_r = False

        m_count = 0
        m_lines = set()
        
        prev_line_is_scroll = False
        prev_scroll_line = None

        # バッチ処理用にタグの適用範囲を収集する辞書
        tag_ranges = {t: [] for t in clear_tags}

        for i, line in enumerate(lines, 1):
            s = line.strip()
            ls = f"{i}.0"
            le = f"{i}.end"
            
            in_score = any(sl < i < el for sl, el, _ in score_ranges)

            if s.startswith("#"):
                tag_ranges["cmd"].extend([ls, le])
                if s.startswith("#SCROLL"):
                    if prev_line_is_scroll:
                        tag_ranges["warn"].extend([f"{prev_scroll_line}.0", f"{prev_scroll_line}.end", ls, le])
                    prev_line_is_scroll = True
                    prev_scroll_line = i
                else:
                    prev_line_is_scroll = False
                continue
                
            if not s or s.startswith("//"):
                prev_line_is_scroll = False
                if "//" in line:
                    ci = line.index("//")
                    tag_ranges["comment"].extend([f"{i}.{ci}", le])
                continue
                
            code = line.split("//")[0]
            if any(c in "0123456789," for c in code):
                prev_line_is_scroll = False

            if in_score:
                for ci, ch in enumerate(code):
                    p  = f"{i}.{ci}"
                    p1 = f"{i}.{ci+1}"
                    if ch in "1234": tag_ranges[f"num_{ch}"].extend([p, p1])
                    elif ch in "09": tag_ranges[f"num_{ch}"].extend([p, p1])
                    elif ch == ",":
                        if m_count > 0 and m_count not in VALID_MEASURE_COUNTS:
                            for ml in m_lines: self.invalid_lines[ml] = m_count
                            self.invalid_lines[i] = m_count
                        m_count = 0; m_lines = set()
                    elif ch in "0123456789":
                        m_count += 1; m_lines.add(i)
            else:
                if ":" in code and not s.startswith("//"):
                    ci = code.index(":")
                    tag_ranges["header_key"].extend([ls, f"{i}.{ci}"])
                    tag_ranges["header_val"].extend([f"{i}.{ci+1}", le])

        # 収集したタグ範囲を一括で適用する（Tkinterの引数制限回避のため1000要素ごとに分割）
        for tag, ranges in tag_ranges.items():
            if ranges:
                for idx in range(0, len(ranges), 1000):
                    ta.tag_add(tag, *ranges[idx:idx+1000])

        courses_dict = {c["key"]: c for c in self.courses_info}
        b_idx = 0
        r_idx = 0
        dyn_tags = []

        for sl, el, cname in score_ranges:
            curr = f"{sl}.0"
            end  = f"{el}.end"
            
            c_info = courses_dict.get(cname, {})
            c_rolls = c_info.get("rolls_info", [])
            c_balloons = c_info.get("balloons_info", [])
            r_local = 0
            b_local = 0
            
            while True:
                pos = ta.search(r"[5-7]", curr, stopindex=end, regexp=True)
                if not pos: break
                ch = ta.get(pos)
                row_text = ta.get(f"{pos} linestart", f"{pos} lineend")
                if row_text.strip().startswith("#"):
                    curr = f"{pos}+1c"
                    continue
                    
                end_pos = ta.search("8", pos, stopindex=end)
                if ch in "56":
                    tag = "roll" if ch == "5" else "roll_big"
                    ri_tag = f"ri_{r_idx}"
                    dyn_tags.append((ri_tag, pos, f"{pos}+1c"))
                    
                    if r_local < len(c_rolls):
                        self.roll_hits[r_idx] = c_rolls[r_local]
                    r_local += 1
                    r_idx += 1
                    
                elif ch == "7":
                    tag = "balloon_tag"
                    bi_tag = f"bi_{b_idx}"
                    dyn_tags.append((bi_tag, pos, f"{pos}+1c"))
                    
                    if b_local < len(c_balloons):
                        self.balloon_hits[b_idx] = c_balloons[b_local]
                    b_local += 1
                    b_idx += 1
                    
                if end_pos:
                    dyn_tags.append((tag, pos, f"{end_pos}+1c"))
                    curr = f"{end_pos}+1c"
                else:
                    dyn_tags.append((tag, pos, f"{pos}+1c"))
                    curr = f"{pos}+1c"

        for t, start, end_idx in dyn_tags:
            ta.tag_configure(t)
            ta.tag_add(t, start, end_idx)

    def _refresh_sidebar(self, content):
        lines = content.split('\n')
        title = ""; subtitle = ""
        for l in lines:
            if l.startswith("TITLE:"): title = l[6:].strip()
            if l.startswith("SUBTITLE:"): subtitle = re.sub(r"^(--|\\+\\+)", "", l[9:].strip())
        self.courses_info = self.analyzer.parse_courses(content)
        self._sb_title.config(text=f"{'  ' + title if title else '  (無題)'}\n  {subtitle}" if subtitle else f"  {'  ' + title if title else '  (無題)'}")
        
        if not hasattr(self, "_course_cards"):
            self._course_cards = {}
            
        visible_keys = [c["key"] for c in self.courses_info]
        
        for course in self.courses_info:
            k = course["key"]
            if k in self._course_cards:
                self._update_course_card(course)
            else:
                self._make_course_card(course)
                
        keys_to_remove = [k for k in self._course_cards if k not in visible_keys]
        for k in keys_to_remove:
            self._course_cards[k]["frame"].destroy()
            del self._course_cards[k]
            
        self._sb_canvas.configure(scrollregion=self._sb_canvas.bbox("all"))

    def _make_course_card(self, course):
        k = course["key"]
        color = course["color"]
        
        card = tk.Frame(self._sb_frame, bg=COLORS["bg3"], highlightthickness=1, highlightbackground=color)
        card.pack(fill="x", padx=6, pady=4)
        
        hdr = tk.Frame(card, bg=color, height=22)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"  {course['label']}", bg=color, fg=COLORS["bg"], font=("Consolas", 9, "bold")).pack(side=tk.LEFT)
        
        body = tk.Frame(card, bg=COLORS["bg3"], padx=8, pady=6)
        body.pack(fill="x")
        
        lbl_time = tk.Label(body, justify="left", bg=COLORS["bg3"], fg=COLORS["fg"], font=("Consolas", 9))
        lbl_time.pack(anchor="w")
        lbl_notes = tk.Label(body, justify="left", bg=COLORS["bg3"], fg=COLORS["fg"], font=("Consolas", 9))
        lbl_notes.pack(anchor="w")
        lbl_measures = tk.Label(body, justify="left", bg=COLORS["bg3"], fg=COLORS["fg"], font=("Consolas", 9))
        lbl_measures.pack(anchor="w")
        lbl_roll = tk.Label(body, justify="left", bg=COLORS["bg3"], fg=COLORS["fg"], font=("Consolas", 9))
        lbl_roll.pack(anchor="w")
        
        details_frm = tk.Frame(body, bg=COLORS["bg3"])
        btn_toggle = tk.Button(body, text="連打詳細 [開く]", bg=COLORS["bg3"], fg=COLORS["accent"], relief="flat", bd=0, font=("Consolas", 9, "underline"), cursor="hand2", activebackground=COLORS["bg3"], activeforeground=COLORS["accent2"])
        
        lbl_balloon = tk.Label(body, justify="left", bg=COLORS["bg3"], fg=COLORS["fg"], font=("Consolas", 9))
        lbl_balloon.pack(anchor="w")

        details_visible = tk.BooleanVar(value=False)
        def toggle_details():
            if details_visible.get():
                details_frm.pack_forget()
                btn_toggle.config(text="連打詳細 [開く]")
                details_visible.set(False)
            else:
                details_frm.pack(fill="x", pady=(2, 0), before=lbl_balloon)
                btn_toggle.config(text="連打詳細 [閉じる]")
                details_visible.set(True)
                
        btn_toggle.config(command=toggle_details)
            
        self._course_cards[k] = {
            "frame": card,
            "lbl_time": lbl_time,
            "lbl_notes": lbl_notes,
            "lbl_measures": lbl_measures,
            "lbl_roll": lbl_roll,
            "details_frm": details_frm,
            "btn_toggle": btn_toggle,
            "lbl_balloon": lbl_balloon,
            "details_visible": details_visible
        }
        
        self._update_course_card(course)

    def _update_course_card(self, course):
        k = course["key"]
        refs = self._course_cards[k]
        
        refs["lbl_time"].config(text=f"時間: {course['time']}")
        refs["lbl_notes"].config(text=f"ノーツ: {course['notes']}")
        refs["lbl_measures"].config(text=f"小節: {course['measures']}")
        
        rolls = course.get("rolls_info", [])
        total_roll_dur = sum(r["duration"] for r in rolls)
        total_roll_hits = sum(r["hits"] for r in rolls)
        refs["lbl_roll"].config(text=f"連打総計: {total_roll_dur:.2f}秒 ({total_roll_hits}打)")
        
        for w in refs["details_frm"].winfo_children():
            w.destroy()
            
        if rolls:
            for i, r in enumerate(rolls, 1):
                tk.Label(refs["details_frm"], text=f"  {i}本目: {r['duration']:.2f}秒 ({r['hits']}打)", bg=COLORS["bg3"], fg=COLORS["fg_dim"], font=("Consolas", 9)).pack(anchor="w")
            refs["btn_toggle"].pack(anchor="w", pady=(4, 0), before=refs["lbl_balloon"])
            if refs["details_visible"].get():
                refs["details_frm"].pack(fill="x", pady=(2, 0), before=refs["lbl_balloon"])
            else:
                refs["details_frm"].pack_forget()
        else:
            refs["btn_toggle"].pack_forget()
            refs["details_frm"].pack_forget()
            
        balloons = course.get("balloons_info", [])
        total_balloon_hits = sum(b["hits"] for b in balloons)
        refs["lbl_balloon"].config(text=f"風船総計: {len(balloons)}個 ({total_balloon_hits}打)")

    def _heavy_tasks(self):
        content = self.text_area.get("1.0", tk.END)
        self._refresh_sidebar(content)
        self.highlight_syntax()
        self._update_line_numbers()
        self._update_status()
        self._after_id = None

    def _force_update(self):
        if self._after_id: self.master.after_cancel(self._after_id)
        self._heavy_tasks()

    def _update_status(self):
        try:
            line_s, col_s = self.text_area.index(tk.INSERT).split('.')
            line_i = int(line_s)
            msg = f"  行 {line_s}  列 {col_s}  │  {self.encoding_var.get().upper()}"
            if line_i in self.invalid_lines: msg += f"  │  ⚠ 不正文字数 ({self.invalid_lines[line_i]})"
            if self.global_warnings: msg += "  │  " + "  ".join(self.global_warnings)
            self.status_bar.config(text=msg)
        except Exception: pass

    def _on_key_release(self, event=None):
        if event and event.keysym in ("Up", "Down", "Left", "Right"):
            self._update_status(); self._draw_ruler()
            return
        if self._after_id: self.master.after_cancel(self._after_id)
        self._after_id = self.master.after(400, self._heavy_tasks)

    def _on_cursor_move(self, event=None):
        self._draw_ruler(); self._update_status()
        self.line_numbers.yview_moveto(self.text_area.yview()[0])

    def _mark_dirty(self, event):
        if event.keysym in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Up", "Down", "Left", "Right"): return
        try:
            li = int(self.text_area.index(tk.INSERT).split('.')[0])
            self.modified_lines.add(li)
        except Exception: pass

    def _zoom(self, event):
        self.font_size += (1 if event.delta > 0 else -1)
        self.font_size = max(6, min(72, self.font_size))
        self.config_data["font_size"] = self.font_size
        self._update_font()
        self.text_area.configure(font=self.mono_font)
        self.line_numbers.configure(font=self.mono_font)
        self._setup_tags()
        self._draw_ruler()
        self._force_update()
        return "break"

    def _on_motion(self, event):
        try:
            idx  = self.text_area.index(f"@{event.x},{event.y}")
            tags = self.text_area.tag_names(idx)
            for t in tags:
                if t.startswith("bi_"):
                    bi = int(t[3:])
                    b_info = self.balloon_hits.get(bi, {})
                    hits = b_info.get("hits", "?")
                    dur = b_info.get("duration", 0.0)
                    self.tooltip.show(f"風船 No.{bi+1} → {dur:.2f}秒 ({hits}打)", event)
                    return
                elif t.startswith("ri_"):
                    ri = int(t[3:])
                    r_info = self.roll_hits.get(ri, {})
                    hits = r_info.get("hits", "?")
                    dur = r_info.get("duration", 0.0)
                    self.tooltip.show(f"連打 No.{ri+1} → {dur:.2f}秒 (想定{hits}打)", event)
                    return
            self.tooltip.hide()
        except Exception: self.tooltip.hide()

    def _toggle_cp(self, event=None):
        try:
            li = int(self.text_area.index(tk.INSERT).split('.')[0])
            if li in self.checkpoints: self.checkpoints.remove(li)
            else: self.checkpoints.add(li)
            self._update_line_numbers()
        except Exception: pass
        return "break"

    def _jump_cp(self, direction):
        if not self.checkpoints: return "break"
        cur = int(self.text_area.index(tk.INSERT).split('.')[0])
        cps = sorted(self.checkpoints)
        if direction == "Up":
            cands = [c for c in cps if c < cur]
            tgt = max(cands) if cands else cps[-1]
        else:
            cands = [c for c in cps if c > cur]
            tgt = min(cands) if cands else cps[0]
        self.text_area.mark_set(tk.INSERT, f"{tgt}.0")
        self.text_area.see(f"{tgt}.0")
        self._on_cursor_move()
        return "break"

    def _insert_custom(self, key):
        text = self.config_data["custom_shortcuts"].get(key, "")
        if text:
            self.text_area.insert(tk.INSERT, text)
            self._force_update()
        return "break"

    def _get_selection(self):
        try:
            s = self.text_area.index(tk.SEL_FIRST)
            e = self.text_area.index(tk.SEL_LAST)
            return s, e, self.text_area.get(s, e)
        except tk.TclError:
            messagebox.showwarning("確認", "範囲を選択してください。", parent=self.master)
            return None, None, None

    def reverse_don_ka(self):
        s, e, txt = self._get_selection()
        if txt is None: return
        new = txt.translate(str.maketrans("1234", "2143"))
        self.text_area.delete(s, e)
        self.text_area.insert(s, new)
        self._force_update()

    def open_scroll_splitter(self):
        s, e, txt = self._get_selection()
        if txt is None: return
        def apply(new_text):
            self.text_area.delete(s, e)
            self.text_area.insert(s, new_text + "\n")
            self._force_update()
        HighSpeedDialog(self, txt, apply)

    def open_measure_converter(self):
        s, e, txt = self._get_selection()
        if txt is None: return
        def apply(new_text):
            self.text_area.delete(s, e)
            self.text_area.insert(s, new_text + "\n")
            self._force_update()
        MeasureConvertDialog(self, txt, apply)

    def open_image_exporter(self):
        if not HAS_PIL:
            messagebox.showerror("エラー", "Pillowライブラリがインストールされていません。\npip install Pillow を実行してください。", parent=self.master)
            return

        content = self.text_area.get("1.0", tk.END)
        if not self.courses_info:
            messagebox.showwarning("警告", "有効なコースが見つかりません。", parent=self.master)
            return

        # デフォルトのコースを決定（先頭のコースを仮指定）
        target_label = self.courses_info[0]["label"]

        # ダイアログを起動
        TJAImagePreviewDialog(self, content, target_label)
        
    def open_strobe_tool(self):
        text_before_cursor = self.text_area.get("1.0", tk.INSERT)
        lines = text_before_cursor.split("\n")
        current_bpm = "120"
        full_text = self.text_area.get("1.0", tk.END).split("\n")
        for line in full_text:
            if line.startswith("BPM:"): current_bpm = line[4:].strip(); break
        for line in reversed(lines):
            match = re.search(r"#BPMCHANGE\s+([0-9.]+)", line)
            if match: current_bpm = match.group(1); break
        def apply(new_text):
            self.text_area.insert(tk.INSERT, new_text + "\n")
            self._force_update()
        StrobeGeneratorDialog(self, current_bpm, apply)

    def toggle_theme(self):
        current = self.config_data.get("theme", "dark")
        self.config_data["theme"] = "light" if current == "dark" else "dark"
        self._save_settings()
        self._apply_theme_colors()

    def _apply_theme_colors(self):
        global COLORS
        COLORS.update(THEMES.get(self.config_data.get("theme", "dark"), THEMES["dark"]))
        self._init_styles()
        self.master.configure(bg=COLORS["bg"])
        for toolbar in (self.toolbar_top, self.toolbar_bottom):
            toolbar.configure(bg=COLORS["bg2"])
            for w in toolbar.winfo_children():
                if isinstance(w, tk.Button):
                    txt = w.cget("text")
                    if "保存" in txt: w.configure(bg=COLORS["accent"], fg=COLORS["fg_bright"], activebackground=COLORS["toolbar_hover"])
                    else: w.configure(bg=COLORS["toolbar_btn"], fg=COLORS["fg"], activebackground=COLORS["toolbar_hover"])
                elif isinstance(w, tk.Label): w.configure(bg=COLORS["bg3"], fg=COLORS["fg_dim"])

        self.bottom_frame.configure(bg=COLORS["bg3"])
        self.btn_theme.configure(bg=COLORS["bg3"], fg=COLORS["fg_dim"], activebackground=COLORS["border"], activeforeground=COLORS["fg"])
        self.text_area.configure(bg=COLORS["bg"], fg=COLORS["fg"], insertbackground=COLORS["cursor"], selectbackground=COLORS["select"], selectforeground=COLORS["fg_bright"])
        self.line_numbers.configure(bg=COLORS["surface"], fg=COLORS["fg_dim"])
        self.status_bar.configure(bg=COLORS["bg3"], fg=COLORS["fg_dim"])
        self.ruler.configure(bg=COLORS["surface"])
        self.sb_outer.configure(bg=COLORS["bg2"])
        self._sb_title.configure(bg=COLORS["bg2"], fg=COLORS["fg"])
        self._sb_frame.configure(bg=COLORS["bg2"])
        self._sb_frame.master.configure(bg=COLORS["bg2"])
        
        self._setup_tags()
        
        self._force_update()

    def _unsaved_check(self):
        if not self.modified_lines: return True
        ans = messagebox.askyesnocancel("確認", "変更を保存しますか？", parent=self.master)
        if ans is True:
            self.save_file()
            return True
        return ans is False

    def new_file(self):
        if not self._unsaved_check(): return
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0", NEW_FILE_TEMPLATE)
        self.current_file = None
        self.modified_lines.clear()
        self.checkpoints.clear()
        self._force_update()

    def open_file(self):
        if not self._unsaved_check(): return
        path = filedialog.askopenfilename(filetypes=[("TJA Files", "*.tja"), ("Text Files", "*.txt")])
        if not path: return
        try:
            # 常にANSI (CP932) で読み込みを試みる
            with open(path, "r", encoding="cp932") as f: content = f.read()
        except UnicodeDecodeError:
            # 万が一UTF-8等だった場合でも読み込み、強制的にANSIとして扱う
            with open(path, "r", encoding="utf-8") as f: content = f.read()
            messagebox.showinfo("文字コード変換", "UTF-8で保存されたファイルを読み込みました。\n次回保存時に自動的にANSI形式で保存されます。", parent=self.master)
            
        self.encoding_var.set("ANSI (cp932)")
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0", content)
        self.current_file = path
        self.modified_lines.clear()
        self.master.title(f"{APP_NAME}  v{VERSION}  —  {os.path.basename(path)}")
        self._force_update()

    def open_folder(self):
        if self.current_file and os.path.exists(self.current_file):
            folder = os.path.dirname(self.current_file)
            if os.name == 'nt': os.startfile(folder)
            elif sys.platform == 'darwin': subprocess.Popen(['open', folder])
            else: subprocess.Popen(['xdg-open', folder])
        else:
            messagebox.showinfo("情報", "ファイルがまだ保存されていません。", parent=self.master)

    def save_file(self):
        if not self.current_file:
            self.save_file_as()
            return
        try:
            # 常にANSI (CP932) で保存。対応していない特殊文字は「?」に置換してエラーを防ぐ
            with open(self.current_file, "w", encoding="cp932", errors="replace") as f:
                f.write(self.text_area.get("1.0", tk.END))
            self.modified_lines.clear()
            self._update_line_numbers()
        except Exception as e: messagebox.showerror("保存エラー", str(e), parent=self.master)

    def save_file_as(self):
        path = filedialog.asksaveasfilename(defaultextension=".tja", filetypes=[("TJA Files", "*.tja"), ("All Files", "*.*")])
        if path:
            self.current_file = path
            self.master.title(f"{APP_NAME}  v{VERSION}  —  {os.path.basename(path)}")
            self.save_file()

    def _on_close(self):
        if self._unsaved_check(): self.master.destroy()

    def _run_app(self, key):
        path = self.config_data["run_config"][key]["path"]
        if path and os.path.exists(path): subprocess.Popen([path])
        else: messagebox.showwarning("警告", f"{key} のパスが未設定です。\nメニュー「設定 → 環境設定...」で設定してください。", parent=self.master)

    def _create_menu(self):
        mb = tk.Menu(self.master, bg=COLORS["bg3"], fg=COLORS["fg"], activebackground=COLORS["accent"], activeforeground=COLORS["fg_bright"])
        self.master.config(menu=mb)
        def sub(label):
            m = tk.Menu(mb, tearoff=0, bg=COLORS["bg3"], fg=COLORS["fg"], activebackground=COLORS["accent"], activeforeground=COLORS["fg_bright"])
            mb.add_cascade(label=label, menu=m)
            return m

        fm = sub("ファイル")
        fm.add_command(label="新規作成", command=self.new_file)
        fm.add_command(label="開く", command=self.open_file)
        fm.add_command(label="別ウィンドウで開く", command=lambda: subprocess.Popen([sys.executable, sys.argv[0]]))
        fm.add_separator()
        fm.add_command(label="上書き保存  Ctrl+S", command=self.save_file)
        fm.add_command(label="名前を付けて保存", command=self.save_file_as)
        fm.add_separator()
        fm.add_command(label="終了", command=self._on_close)

        tm = sub("ツール")
        tm.add_command(label="ハイスピ変換", command=self.open_scroll_splitter)
        tm.add_command(label="ノーツ間隔リサイズ", command=self.open_measure_converter)
        tm.add_separator()
        tm.add_command(label="あべこべ反転  Ctrl+M", command=self.reverse_don_ka)

        rm = sub("起動")
        for k in ("F1", "F2", "F3"): rm.add_command(label=f"{k}: {self.config_data['run_config'][k]['name']}", command=lambda key=k: self._run_app(key))

        sm = sub("設定")
        sm.add_command(label="環境設定...", command=self._open_settings)

        hm = sub("ヘルプ")
        hm.add_command(label="ヘルプを表示", command=lambda: HelpWindow(self.master))
        hm.add_command(label="バージョン情報", command=lambda: messagebox.showinfo("バージョン情報", f"{APP_NAME}\nVersion: {VERSION}\n\nRedesigned & Optimized Edition", parent=self.master))

    def _open_settings(self):
        win = Toplevel(self.master)
        win.title("環境設定")
        win.geometry("640x760")
        win.configure(bg=COLORS["bg"])
        win.transient(self.master)
        win.grab_set()

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        f_run = tk.Frame(nb, bg=COLORS["bg"])
        nb.add(f_run, text="シミュレータ起動")
        tk.Label(f_run, text="F1〜F3キーで起動するシミュレータの名前とexeパスを設定します。", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9)).grid(row=0, column=0, columnspan=5, sticky="w", padx=8, pady=(12, 4))
        run_entries = {}
        for row, key in enumerate(("F1", "F2", "F3"), start=1):
            tk.Label(f_run, text=f"{key} 名前", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=row, column=0, padx=8, pady=8, sticky="e")
            ne = tk.Entry(f_run, width=12, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10), insertbackground=COLORS["cursor"])
            ne.insert(0, self.config_data["run_config"][key]["name"])
            ne.grid(row=row, column=1, padx=4)
            tk.Label(f_run, text="パス", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=row, column=2, padx=4, sticky="e")
            pe = tk.Entry(f_run, width=32, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10), insertbackground=COLORS["cursor"])
            pe.insert(0, self.config_data["run_config"][key]["path"])
            pe.grid(row=row, column=3, padx=4)
            def browse(ent=pe):
                p = filedialog.askopenfilename(parent=win)
                if p: ent.delete(0, tk.END); ent.insert(0, p)
            tk.Button(f_run, text="参照", command=browse, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 9), relief="flat").grid(row=row, column=4, padx=4)
            run_entries[key] = (ne, pe)

        f_sc = tk.Frame(nb, bg=COLORS["bg"])
        nb.add(f_sc, text="ショートカット")
        tk.Label(f_sc, text="Alt + 数字キーを押した際に即座に入力されるカスタムコマンドや文字列を設定します。", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 9)).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(12, 4))
        sc_entries = {}
        for i in range(10):
            r = (i % 5) + 1; c = (i // 5) * 2
            tk.Label(f_sc, text=f"Alt + {i}", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=r, column=c, padx=8, pady=8, sticky="e")
            ent = tk.Entry(f_sc, width=18, bg=COLORS["surface"], fg=COLORS["fg"], font=("Consolas", 10), insertbackground=COLORS["cursor"])
            ent.insert(0, self.config_data["custom_shortcuts"].get(str(i), ""))
            ent.grid(row=r, column=c+1, padx=4, pady=8)
            sc_entries[str(i)] = ent

        f_ed = tk.Frame(nb, bg=COLORS["bg"])
        nb.add(f_ed, text="エディタ・ツール")
        
        tk.Label(f_ed, text="フォント", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=0, column=0, padx=8, pady=(12, 2), sticky="e")
        fonts = list(font.families())
        v_font_fam = tk.StringVar(value=self.config_data.get("font_family", "Consolas"))
        ttk.Combobox(f_ed, values=fonts, textvariable=v_font_fam, state="readonly", width=22).grid(row=0, column=1, sticky="w", pady=(12, 2))
        tk.Label(f_ed, text="エディタの表示に使用するフォントです。", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 8)).grid(row=1, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")
        
        tk.Label(f_ed, text="基本フォントサイズ", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=2, column=0, padx=8, pady=(6, 2), sticky="e")
        v_font = tk.StringVar(value=str(self.config_data.get("font_size", 12)))
        tk.Spinbox(f_ed, from_=6, to=72, textvariable=v_font, width=6, bg=COLORS["surface"], fg=COLORS["fg"], buttonbackground=COLORS["surface"]).grid(row=2, column=1, sticky="w", pady=(6, 2))
        tk.Label(f_ed, text="起動時の文字サイズです。（エディタ上でCtrl+ホイールでも一時変更可能）", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 8)).grid(row=3, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")
        
        v_ext = tk.BooleanVar(value=self.config_data.get("resize_ext", False))
        tk.Checkbutton(f_ed, text="リサイズ時に256分以上の分解能をデフォルトで表示", variable=v_ext, bg=COLORS["bg"], fg=COLORS["fg"], selectcolor=COLORS["surface"], font=("Consolas", 10)).grid(row=4, column=0, columnspan=2, padx=8, pady=(6, 2), sticky="w")
        tk.Label(f_ed, text="リサイズ機能のダイアログを開いた際、256分以上の細かい分解能を最初からリストに表示します。", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 8)).grid(row=5, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")
        
        tk.Label(f_ed, text="リサイズ折り返し(16の倍数)", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=6, column=0, padx=8, pady=(6, 2), sticky="e")
        v_w16 = ttk.Combobox(f_ed, values=["16", "32", "改行なし"], state="readonly", width=12)
        v_w16.set(str(self.config_data.get("resize_wrap_16", 16)))
        v_w16.grid(row=6, column=1, sticky="w", pady=(6, 2))
        tk.Label(f_ed, text="16分や32分音符などにリサイズした際、指定文字数で自動改行して視認性を保ちます。", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 8)).grid(row=7, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")
        
        tk.Label(f_ed, text="リサイズ折り返し(12の倍数)", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=8, column=0, padx=8, pady=(6, 2), sticky="e")
        v_w12 = ttk.Combobox(f_ed, values=["12", "24", "48", "改行なし"], state="readonly", width=12)
        v_w12.set(str(self.config_data.get("resize_wrap_12", 24)))
        v_w12.grid(row=8, column=1, sticky="w", pady=(6, 2))
        tk.Label(f_ed, text="12分や24分音符などにリサイズした際、指定文字数で自動改行して視認性を保ちます。", bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 8)).grid(row=9, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")
        
        tk.Label(f_ed, text="0.1秒未満の連打処理", bg=COLORS["bg"], fg=COLORS["fg"], font=("Consolas", 10)).grid(row=10, column=0, padx=8, pady=(6, 2), sticky="e")
        v_comp = ttk.Combobox(f_ed, values=["通常計算", "段階的補正 (60fps理論値)", "段階的補正 (理論値-1)"], state="readonly", width=22)
        v_comp.set(self.config_data.get("short_roll_comp", "段階的補正 (60fps理論値)"))
        v_comp.grid(row=10, column=1, sticky="w", pady=(6, 2))
        
        desc_text = (
            "極端に短い連打に対するシミュレータの仕様を再現する補正モードです。\n"
            "・通常計算 : 常に (秒数 × 左パネルの連打秒速) で計算します。\n"
            "・60fps理論値 : 0.1秒以下=秒速60、0.15秒以下=秒速55 で計算します。\n"
            "・理論値-1 : 0.1秒以下=秒速55、0.15秒以下=秒速50 で計算します。\n"
            "※設定した「連打秒速」が上記の補正値を上回る場合は、設定値（高い方）が優先されます。"
        )
        tk.Label(f_ed, text=desc_text, bg=COLORS["bg"], fg=COLORS["fg_dim"], font=("Consolas", 8), justify="left").grid(row=11, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")

        def save_cfg():
            for k, (n, p) in run_entries.items():
                self.config_data["run_config"][k]["name"] = n.get(); self.config_data["run_config"][k]["path"] = p.get()
            for k, ent in sc_entries.items(): self.config_data["custom_shortcuts"][k] = ent.get()
            self.config_data["font_family"] = v_font_fam.get()
            try: self.config_data["font_size"] = int(v_font.get())
            except ValueError: pass
            self.config_data["resize_ext"] = v_ext.get()
            
            w16_val = v_w16.get()
            self.config_data["resize_wrap_16"] = int(w16_val) if w16_val.isdigit() else "改行なし"
            w12_val = v_w12.get()
            self.config_data["resize_wrap_12"] = int(w12_val) if w12_val.isdigit() else "改行なし"
                
            self.config_data["short_roll_comp"] = v_comp.get()

            self._save_settings()
            self._create_menu()
            self.font_family = self.config_data["font_family"]
            self.font_size = self.config_data["font_size"]
            self._update_font()
            self.text_area.configure(font=self.mono_font)
            self.line_numbers.configure(font=self.mono_font)
            self._setup_tags()
            self._draw_ruler()
            self._force_update()
            win.destroy()

        def reset_cfg():
            if messagebox.askyesno("確認", "すべての環境設定を初期化しますか？", parent=win):
                self.config_data = {
                    "run_config": {k: {"name": f"シミュレータ{k}", "path": ""} for k in ("F1", "F2", "F3")},
                    "custom_shortcuts": {str(i): "" for i in range(10)},
                    "theme": "dark",
                    "font_family": "Consolas",
                    "font_size": 12,
                    "resize_ext": False,
                    "resize_wrap_16": 16,
                    "resize_wrap_12": 24,
                    "roll_speed": 45,
                    "short_roll_comp": "段階的補正 (60fps理論値)"
                }
                self._save_settings()
                self._apply_theme_colors()
                self.font_family = self.config_data["font_family"]
                self.font_size = self.config_data["font_size"]
                self.v_roll_speed.set("45")
                self._update_font()
                self.text_area.configure(font=self.mono_font)
                self.line_numbers.configure(font=self.mono_font)
                self._setup_tags()
                self._draw_ruler()
                self._force_update()
                win.destroy()

        btn_frm = tk.Frame(win, bg=COLORS["bg"])
        btn_frm.pack(side=tk.BOTTOM, fill="x", pady=12, padx=16)
        tk.Button(btn_frm, text="初期化", command=reset_cfg, bg=COLORS["err"], fg="#ffffff", font=("Consolas", 10), relief="flat", padx=12, pady=6).pack(side=tk.LEFT)
        tk.Button(btn_frm, text="保存して適用", command=save_cfg, bg=COLORS["accent"], fg=COLORS["fg_bright"], font=("Consolas", 10, "bold"), relief="flat", padx=16, pady=6).pack(side=tk.RIGHT)

    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f: data = json.load(f)
                for key in ("run_config", "custom_shortcuts", "theme", "font_family", "font_size", "resize_ext", "resize_wrap_16", "resize_wrap_12", "roll_speed", "short_roll_comp"):
                    if key in data:
                        if isinstance(data[key], dict) and isinstance(self.config_data[key], dict): self.config_data[key].update(data[key])
                        else: self.config_data[key] = data[key]
            except: pass

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f: json.dump(self.config_data, f, indent=2, ensure_ascii=False)
        except: pass

if __name__ == "__main__":
    root = tk.Tk()
    app  = NeoTJAEditor(root)
    root.mainloop()