# -*- coding: utf-8 -*-
"""计时器软件：包含倒计时 和 秒表 两种模式，使用 tkinter 实现 UI。"""

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


class TimerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("计时器")
        self.root.geometry("420x360")
        self.root.resizable(False, False)

        # 状态变量
        self.mode = "countdown"           # "countdown" 或 "stopwatch"
        self.running = False
        self.remaining = 0                # 倒计时剩余秒数
        self.elapsed = 0.0                # 秒表已过秒数
        self.after_id = None
        self.last_tick = None             # 用于秒表精确计时

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style()
        style.configure("Big.TLabel", font=("Consolas", 56), foreground="#222")

        # 模式切换
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_countdown = ttk.Frame(self.notebook, padding=20)
        self.tab_stopwatch = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.tab_countdown, text="倒计时")
        self.notebook.add(self.tab_stopwatch, text="秒表")

        self._build_countdown_tab()
        self._build_stopwatch_tab()

    def _build_countdown_tab(self):
        # 时间显示
        self.cd_label = ttk.Label(self.tab_countdown, text="00:00:00",
                                  style="Big.TLabel")
        self.cd_label.pack(pady=10)

        # 输入区
        input_frame = ttk.Frame(self.tab_countdown)
        input_frame.pack(pady=10)

        self.h_var = tk.StringVar(value="0")
        self.m_var = tk.StringVar(value="0")
        self.s_var = tk.StringVar(value="0")

        for i, (var, text) in enumerate(
            [(self.h_var, "时"), (self.m_var, "分"), (self.s_var, "秒")]
        ):
            ttk.Label(input_frame, text=text).grid(row=0, column=i * 2, padx=2)
            ttk.Spinbox(input_frame, from_=0, to=99, width=4,
                        textvariable=var, justify="center").grid(
                row=1, column=i * 2, padx=2)

        # 按钮
        btn_frame = ttk.Frame(self.tab_countdown)
        btn_frame.pack(pady=10)
        self.cd_start_btn = ttk.Button(btn_frame, text="开始", width=8,
                                       command=self.start_countdown)
        self.cd_pause_btn = ttk.Button(btn_frame, text="暂停", width=8,
                                       command=self.pause_countdown,
                                       state="disabled")
        self.cd_reset_btn = ttk.Button(btn_frame, text="重置", width=8,
                                       command=self.reset_countdown)
        self.cd_start_btn.grid(row=0, column=0, padx=5)
        self.cd_pause_btn.grid(row=0, column=1, padx=5)
        self.cd_reset_btn.grid(row=0, column=2, padx=5)

    def _build_stopwatch_tab(self):
        self.sw_label = ttk.Label(self.tab_stopwatch, text="00:00:00.0",
                                  style="Big.TLabel")
        self.sw_label.pack(pady=10)

        btn_frame = ttk.Frame(self.tab_stopwatch)
        btn_frame.pack(pady=10)
        self.sw_start_btn = ttk.Button(btn_frame, text="开始", width=8,
                                       command=self.start_stopwatch)
        self.sw_pause_btn = ttk.Button(btn_frame, text="暂停", width=8,
                                       command=self.pause_stopwatch,
                                       state="disabled")
        self.sw_reset_btn = ttk.Button(btn_frame, text="重置", width=8,
                                       command=self.reset_stopwatch)
        self.sw_start_btn.grid(row=0, column=0, padx=5)
        self.sw_pause_btn.grid(row=0, column=1, padx=5)
        self.sw_reset_btn.grid(row=0, column=2, padx=5)

        # 计次列表
        list_frame = ttk.Frame(self.tab_stopwatch)
        list_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.lap_list = tk.Listbox(list_frame, height=6, font=("Consolas", 12))
        self.lap_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical",
                                  command=self.lap_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.lap_list.config(yscrollcommand=scrollbar.set)

        ttk.Button(self.tab_stopwatch, text="计次",
                   command=self.record_lap).pack(pady=5)

    # ---------- 工具 ----------
    def _format_hms(self, total_seconds):
        h = int(total_seconds // 3600)
        m = int((total_seconds % 3600) // 60)
        s = int(total_seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

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

    def _tick(self):
        """每秒执行一次的刷新逻辑。"""
        if not self.running:
            return
        if self.mode == "countdown":
            self.remaining -= 1
            self.cd_label.config(text=self._format_hms(max(self.remaining, 0)))
            if self.remaining <= 0:
                self._finish_countdown()
                return
        else:
            now = time.perf_counter()
            self.elapsed += now - self.last_tick
            self.last_tick = now
            self.sw_label.config(text=self._format_sw(self.elapsed))

        self.after_id = self.root.after(200, self._tick)

    def _format_sw(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        tenths = int((seconds * 10) % 10)
        return f"{h:02d}:{m:02d}:{s:02d}.{tenths}"

    def _stop_tick(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    # ---------- 倒计时逻辑 ----------
    def start_countdown(self):
        if self.running:
            return
        if self.remaining == 0:
            total = self._parse_input()
            if total is None:
                messagebox.showwarning("提示", "请输入有效的时分秒（分/秒 0-59）。")
                return
            if total == 0:
                messagebox.showwarning("提示", "倒计时时长不能为 0。")
                return
            self.remaining = total
            self.cd_label.config(text=self._format_hms(total))
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
        self.cd_start_btn.config(state="normal", text="继续")
        self.cd_pause_btn.config(state="disabled")

    def reset_countdown(self):
        self.running = False
        self._stop_tick()
        self.remaining = 0
        self.cd_label.config(text="00:00:00")
        self.cd_start_btn.config(state="normal", text="开始")
        self.cd_pause_btn.config(state="disabled")

    def _finish_countdown(self):
        self.running = False
        self.remaining = 0
        self.cd_start_btn.config(state="normal", text="开始")
        self.cd_pause_btn.config(state="disabled")
        beep()
        messagebox.showinfo("计时器", "倒计时结束！")

    # ---------- 秒表逻辑 ----------
    def start_stopwatch(self):
        if self.running:
            return
        self.mode = "stopwatch"
        self.running = True
        self.last_tick = time.perf_counter()
        self.sw_start_btn.config(state="disabled")
        self.sw_pause_btn.config(state="normal")
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
        self.sw_start_btn.config(state="normal")
        self.sw_pause_btn.config(state="disabled")

    def reset_stopwatch(self):
        self.running = False
        self._stop_tick()
        self.elapsed = 0.0
        self.sw_label.config(text="00:00:00.0")
        self.lap_list.delete(0, tk.END)
        self.sw_start_btn.config(state="normal")
        self.sw_pause_btn.config(state="disabled")

    def record_lap(self):
        if self.mode != "stopwatch":
            return
        self.lap_list.insert(tk.END, self._format_sw(self.elapsed))
        self.lap_list.see(tk.END)

    # ---------- 关闭 ----------
    def on_close(self):
        self._stop_tick()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = TimerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
