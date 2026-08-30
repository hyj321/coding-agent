# -*- coding: utf-8 -*-
"""计时器软件：包含倒计时 和 秒表 两种模式，使用 tkinter 实现 UI。

UI 特点：
- 圆形进度环（Canvas 弧线）实时展示剩余/已用比例
- 扁平化按钮 + 悬停高亮
- Catppuccin Mocha 深色配色
"""

import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import winsound  # Windows 下的提示音

    def beep():
        winsound.Beep(880, 300)
        winsound.Beep(660, 300)
except ImportError:  # macOS / Linux 使用终端提示音

    def beep():
        sys.stdout.write("\a")
        sys.stdout.flush()


# ---------- 配色（Catppuccin Mocha） ----------
BG       = "#1e1e2e"   # 窗口背景
BG_CARD  = "#313244"   # 卡片 / 选中标签
BG_IN    = "#181825"   # 输入框 / 列表背景
BG_HOVER = "#45475a"   # 按钮悬停
ACCENT   = "#89b4fa"   # 主色（蓝）
GREEN    = "#a6e3a1"   # 开始
YELLOW   = "#f9e2af"   # 暂停
RED      = "#f38ba8"   # 结束提示
FG       = "#cdd6f4"   # 主文字
FG_MUT   = "#a6adc8"   # 次级文字
FG_DIM   = "#6c7086"   # 弱化文字

# ---------- 字体 ----------
FONT_FAMILY = "Segoe UI"
FONT_MONO   = "Consolas"
FONT       = (FONT_FAMILY, 10)
FONT_TITLE = (FONT_FAMILY, 17, "bold")
FONT_SUB   = (FONT_FAMILY, 9)
FONT_B     = (FONT_FAMILY, 11, "bold")
FONT_D     = (FONT_MONO, 32, "bold")
FONT_LAP   = (FONT_MONO, 12)


class TimerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("计时器")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(480, 560)

        # 状态变量
        self.mode = "countdown"           # "countdown" 或 "stopwatch"
        self.running = False
        self.remaining = 0                # 倒计时剩余秒数
        self.cd_total = 0                 # 倒计时总秒数
        self.elapsed = 0.0                # 秒表已过秒数
        self.after_id = None
        self.last_tick = None             # 用于秒表精确计时

        self._setup_styles()
        self._build_ui()
        self._center_window(520, 620)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 样式 ----------
    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Timer.TFrame", background=BG)

        style.configure("Timer.TNotebook", background=BG,
                        borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("Timer.TNotebook.Tab",
                        background=BG_IN, foreground=FG_MUT,
                        font=FONT_B, padding=(34, 10), borderwidth=0)
        style.map("Timer.TNotebook.Tab",
                  background=[("selected", BG_CARD)],
                  foreground=[("selected", FG)])

        style.configure("Timer.TSpinbox",
                        fieldbackground=BG_IN, background=BG_IN,
                        foreground=FG, arrowcolor=FG,
                        bordercolor=BG_IN, lightcolor=BG_IN,
                        darkcolor=BG_IN, insertcolor=FG,
                        padding=6)
        style.map("Timer.TSpinbox",
                  bordercolor=[("focus", ACCENT)],
                  fieldbackground=[("focus", BG_IN)])

        style.configure("Timer.Vertical.TScrollbar",
                        background=BG_CARD, troughcolor=BG_IN,
                        bordercolor=BG_IN, arrowcolor=FG_MUT)
        style.map("Timer.Vertical.TScrollbar",
                  background=[("active", BG_HOVER)])

    # ---------- 窗口 ----------
    def _center_window(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- UI 辅助 ----------
    def _make_button(self, parent, text, command, bg, fg, active_bg, width=6):
        """扁平化现代风格按钮（带悬停高亮）。"""
        btn = tk.Button(
            parent, text=text, command=command,
            font=FONT_B, bg=bg, fg=fg,
            activebackground=active_bg, activeforeground=fg,
            disabledforeground=FG_DIM,
            relief="flat", bd=0, highlightthickness=0,
            cursor="hand2", padx=16, pady=8, width=width)
        btn.bind("<Enter>", lambda e: btn.config(bg=active_bg))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        return btn

    def _make_ring(self, parent, size=250, thickness=14):
        """圆形进度环，返回 (canvas, arc_id)。"""
        canvas = tk.Canvas(parent, width=size, height=size,
                           bg=BG, highlightthickness=0, bd=0)
        pad = thickness + 10
        x0, y0, x1, y1 = pad, pad, size - pad, size - pad
        canvas.create_oval(x0, y0, x1, y1, outline=BG_CARD, width=thickness)
        arc = canvas.create_arc(x0, y0, x1, y1, start=90, extent=0,
                                style="arc", outline=ACCENT, width=thickness)
        return canvas, arc

    @staticmethod
    def _set_ring(canvas, arc, fraction, color):
        """更新进度环：fraction 0.0~1.0（1.0 表示画满一圈）。"""
        frac = max(0.0, min(1.0, fraction))
        extent = -359.9 if frac >= 1.0 else -359.9 * frac
        canvas.itemconfig(arc, extent=extent, outline=color)

    # ---------- 主界面 ----------
    def _build_ui(self):
        # 标题
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(18, 4))
        tk.Label(header, text="计时器", font=FONT_TITLE, bg=BG, fg=FG).pack(anchor="w")
        tk.Label(header, text="倒计时 · 秒表", font=FONT_SUB, bg=BG, fg=FG_MUT).pack(anchor="w")

        # 标签页
        self.notebook = ttk.Notebook(self.root, style="Timer.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(10, 16))

        self.tab_countdown = ttk.Frame(self.notebook, style="Timer.TFrame",
                                       padding=(24, 20))
        self.tab_stopwatch = ttk.Frame(self.notebook, style="Timer.TFrame",
                                       padding=(24, 20))
        self.notebook.add(self.tab_countdown, text="倒计时")
        self.notebook.add(self.tab_stopwatch, text="秒表")

        self._build_countdown_tab()
        self._build_stopwatch_tab()

    # ---------- 倒计时页 ----------
    def _build_countdown_tab(self):
        # 圆形进度环 + 时间显示（时间放在环中央）
        self.cd_canvas, self.cd_arc = self._make_ring(self.tab_countdown, size=250)
        self.cd_canvas.pack(pady=(0, 4))

        self.cd_label = tk.Label(self.tab_countdown, text="00:00:00",
                                 font=FONT_D, bg=BG, fg=ACCENT)
        self.cd_canvas.create_window(125, 125, window=self.cd_label)
        self._set_ring(self.cd_canvas, self.cd_arc, 1.0, ACCENT)

        # 时分秒输入
        input_frame = tk.Frame(self.tab_countdown, bg=BG)
        input_frame.pack(pady=(18, 0))
        self.h_var = tk.StringVar(value="00")
        self.m_var = tk.StringVar(value="05")
        self.s_var = tk.StringVar(value="00")
        specs = (("时", self.h_var, 99), ("分", self.m_var, 59), ("秒", self.s_var, 59))
        for i, (label, var, to) in enumerate(specs):
            unit = tk.Frame(input_frame, bg=BG)
            unit.grid(row=0, column=i, padx=10)
            tk.Label(unit, text=label, font=FONT_SUB, bg=BG, fg=FG_MUT).pack()
            spin = ttk.Spinbox(unit, from_=0, to=to, textvariable=var,
                               width=4, justify="center", font=FONT_B,
                               style="Timer.TSpinbox")
            spin.pack(pady=(4, 0))
            spin.bind("<Return>", lambda e: self.start_countdown())

        # 按钮
        btn_frame = tk.Frame(self.tab_countdown, bg=BG)
        btn_frame.pack(pady=(22, 0))
        self.cd_start_btn = self._make_button(
            btn_frame, "开始", self.start_countdown, GREEN, "#11111b", "#8fd48f")
        self.cd_pause_btn = self._make_button(
            btn_frame, "暂停", self.pause_countdown, YELLOW, "#11111b", "#f5e0b7")
        self.cd_reset_btn = self._make_button(
            btn_frame, "重置", self.reset_countdown, BG_CARD, FG, BG_HOVER)
        for b in (self.cd_start_btn, self.cd_pause_btn, self.cd_reset_btn):
            b.pack(side="left", padx=8)
        self.cd_pause_btn.config(state="disabled")

    # ---------- 秒表页 ----------
    def _build_stopwatch_tab(self):
        # 圆形进度环 + 时间显示
        self.sw_canvas, self.sw_arc = self._make_ring(self.tab_stopwatch, size=250)
        self.sw_canvas.pack(pady=(0, 4))

        self.sw_label = tk.Label(self.tab_stopwatch, text="00:00:00.0",
                                 font=FONT_D, bg=BG, fg=GREEN)
        self.sw_canvas.create_window(125, 125, window=self.sw_label)
        self._set_ring(self.sw_canvas, self.sw_arc, 0.0, GREEN)

        # 按钮
        btn_frame = tk.Frame(self.tab_stopwatch, bg=BG)
        btn_frame.pack(pady=(18, 0))
        self.sw_start_btn = self._make_button(
            btn_frame, "开始", self.start_stopwatch, GREEN, "#11111b", "#8fd48f")
        self.sw_pause_btn = self._make_button(
            btn_frame, "暂停", self.pause_stopwatch, YELLOW, "#11111b", "#f5e0b7")
        self.sw_lap_btn = self._make_button(
            btn_frame, "计次", self.record_lap, ACCENT, "#11111b", "#a6c8ff")
        self.sw_reset_btn = self._make_button(
            btn_frame, "重置", self.reset_stopwatch, BG_CARD, FG, BG_HOVER)
        for b in (self.sw_start_btn, self.sw_pause_btn,
                  self.sw_lap_btn, self.sw_reset_btn):
            b.pack(side="left", padx=8)
        self.sw_pause_btn.config(state="disabled")
        self.sw_lap_btn.config(state="disabled")

        # 计次列表
        list_frame = tk.Frame(self.tab_stopwatch, bg=BG_CARD)
        list_frame.pack(fill="both", expand=True, pady=(16, 0))
        tk.Label(list_frame, text="计次", font=FONT_SUB, bg=BG_CARD, fg=FG_MUT) \
            .pack(anchor="w", padx=12, pady=(10, 4))
        inner = tk.Frame(list_frame, bg=BG_CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.lap_list = tk.Listbox(
            inner, height=6, font=FONT_LAP,
            bg=BG_IN, fg=FG,
            selectbackground=ACCENT, selectforeground=BG,
            relief="flat", bd=0, highlightthickness=0, activestyle="none")
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.lap_list.yview,
                           style="Timer.Vertical.TScrollbar")
        self.lap_list.configure(yscrollcommand=sb.set)
        self.lap_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------- 工具 ----------
    def _format_hms(self, total_seconds):
        total_seconds = max(0, int(total_seconds))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _format_sw(self, seconds):
        seconds = max(0.0, seconds)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        tenths = int((seconds * 10) % 10)
        return f"{h:02d}:{m:02d}:{s:02d}.{tenths}"

    def _parse_input(self):
        try:
            h = int(self.h_var.get())
            m = int(self.m_var.get())
            s = int(self.s_var.get())
        except ValueError:
            return None
        if h < 0 or m < 0 or s < 0 or m > 59 or s > 59:
            return None
        return h * 3600 + m * 60 + s

    def _stop_tick(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def _tick(self):
        """周期刷新逻辑：倒计时 1 秒一次，秒表 0.1 秒一次。"""
        if not self.running:
            return
        if self.mode == "countdown":
            self.remaining -= 1
            if self.remaining <= 0:
                self.remaining = 0
                self.cd_label.config(text="00:00:00", fg=RED)
                self._set_ring(self.cd_canvas, self.cd_arc, 0.0, RED)
                self._finish_countdown()
                return
            self.cd_label.config(text=self._format_hms(self.remaining))
            frac = self.remaining / self.cd_total if self.cd_total > 0 else 0.0
            self._set_ring(self.cd_canvas, self.cd_arc, frac, ACCENT)
            self.after_id = self.root.after(1000, self._tick)
        else:
            now = time.perf_counter()
            self.elapsed = now - self.last_tick
            self.sw_label.config(text=self._format_sw(self.elapsed))
            frac = (self.elapsed % 60.0) / 60.0
            self._set_ring(self.sw_canvas, self.sw_arc, frac, GREEN)
            self.after_id = self.root.after(100, self._tick)

    # ---------- 倒计时 ----------
    def start_countdown(self):
        if self.running:
            return
        if self.remaining <= 0:
            total = self._parse_input()
            if total is None:
                messagebox.showwarning("提示", "请输入有效的时分秒（分/秒 0-59）。")
                return
            if total == 0:
                messagebox.showwarning("提示", "倒计时时长不能为 0。")
                return
            self.remaining = total
            self.cd_total = total
            self.cd_label.config(text=self._format_hms(total), fg=ACCENT)
            self._set_ring(self.cd_canvas, self.cd_arc, 1.0, ACCENT)
        else:
            frac = self.remaining / self.cd_total if self.cd_total > 0 else 0.0
            self._set_ring(self.cd_canvas, self.cd_arc, frac, ACCENT)
        self.mode = "countdown"
        self.running = True
        self.cd_start_btn.config(state="disabled")
        self.cd_pause_btn.config(state="normal")
        self.after_id = self.root.after(1000, self._tick)

    def pause_countdown(self):
        if not self.running or self.mode != "countdown":
            return
        self.running = False
        self._stop_tick()
        self.cd_start_btn.config(state="normal")
        self.cd_pause_btn.config(state="disabled")
        frac = self.remaining / self.cd_total if self.cd_total > 0 else 0.0
        self._set_ring(self.cd_canvas, self.cd_arc, frac, FG_MUT)

    def reset_countdown(self):
        self.running = False
        self._stop_tick()
        self.remaining = 0
        total = self._parse_input()
        if total is None:
            self.cd_label.config(text="00:00:00", fg=FG_MUT)
            self._set_ring(self.cd_canvas, self.cd_arc, 0.0, FG_DIM)
        else:
            self.cd_label.config(text=self._format_hms(total), fg=ACCENT)
            self._set_ring(self.cd_canvas, self.cd_arc, 1.0, ACCENT)
        self.cd_start_btn.config(state="normal")
        self.cd_pause_btn.config(state="disabled")

    def _finish_countdown(self):
        self.running = False
        beep()
        beep()
        messagebox.showinfo("时间到", "倒计时结束！")
        self.cd_start_btn.config(state="normal")
        self.cd_pause_btn.config(state="disabled")

    # ---------- 秒表 ----------
    def start_stopwatch(self):
        if self.running:
            return
        self.mode = "stopwatch"
        self.running = True
        if self.elapsed == 0.0:
            self.sw_label.config(text="00:00:00.0", fg=GREEN)
            self._set_ring(self.sw_canvas, self.sw_arc, 0.0, GREEN)
        self.last_tick = time.perf_counter()
        self.sw_start_btn.config(state="disabled")
        self.sw_pause_btn.config(state="normal")
        self.sw_lap_btn.config(state="normal")
        self.after_id = self.root.after(100, self._tick)

    def pause_stopwatch(self):
        if not self.running or self.mode != "stopwatch":
            return
        self.running = False
        self._stop_tick()
        # 补上最后一段未计入的时间
        now = time.perf_counter()
        self.elapsed += now - self.last_tick
        self.sw_label.config(text=self._format_sw(self.elapsed))
        frac = (self.elapsed % 60.0) / 60.0
        self._set_ring(self.sw_canvas, self.sw_arc, frac, FG_MUT)
        self.sw_start_btn.config(state="normal")
        self.sw_pause_btn.config(state="disabled")
        self.sw_lap_btn.config(state="disabled")

    def reset_stopwatch(self):
        self.running = False
        self._stop_tick()
        self.elapsed = 0.0
        self.sw_label.config(text="00:00:00.0", fg=GREEN)
        self._set_ring(self.sw_canvas, self.sw_arc, 0.0, GREEN)
        self.lap_list.delete(0, tk.END)
        self.sw_start_btn.config(state="normal")
        self.sw_pause_btn.config(state="disabled")
        self.sw_lap_btn.config(state="disabled")

    def record_lap(self):
        if not self.running or self.mode != "stopwatch":
            return
        n = self.lap_list.size() + 1
        self.lap_list.insert(tk.END, f"{n:02d}  ·  {self._format_sw(self.elapsed)}")
        self.lap_list.see(tk.END)

    # ---------- 退出 ----------
    def on_close(self):
        self.running = False
        self._stop_tick()
        self.root.destroy()


def main():
    root = tk.Tk()
    TimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
