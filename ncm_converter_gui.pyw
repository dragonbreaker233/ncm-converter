#!/usr/bin/env python3
"""NCM Converter GUI - 网易云音乐 NCM 格式解密工具（图形界面版）

打开应用 → 拖入 .ncm 文件 → 自动转换为 MP3/FLAC
"""

import sys
import os
import threading
import queue
import traceback
from pathlib import Path

# ── 导入核心解密逻辑 ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ncm_converter import (
    decrypt_ncm_header,
    decrypt_audio_stream,
    generate_filename,
    detect_format,
    NotNCMFileError,
    DecryptionError,
)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES


# ── 颜色主题 ──────────────────────────────────────────────────────
BG = "#1e1e2e"           # 主背景
BG2 = "#282840"          # 面板背景
FG = "#cdd6f4"           # 文字
ACCENT = "#89b4fa"       # 强调色
GREEN = "#a6e3a1"        # 成功
RED = "#f38ba8"          # 失败
YELLOW = "#f9e2af"       # 警告
BORDER = "#45475a"       # 边框


class NCMConverterGUI:
    """NCM 转换器图形界面."""

    def __init__(self):
        self.root = TkinterDnD.Tk()
        self.root.title("NCM → MP3 转换器")
        self.root.geometry("680x520")
        self.root.minsize(500, 400)
        self.root.configure(bg=BG)

        # 状态
        self.output_dir = tk.StringVar(value="")
        self.extract_cover = tk.BooleanVar(value=False)
        self.convert_queue = queue.Queue()
        self.is_converting = False
        self.total_files = 0
        self.done_files = 0
        self.fail_files = 0

        self._build_ui()
        self._poll_queue()

        # 窗口居中
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    # ── UI 构建 ──────────────────────────────────────────────────

    def _build_ui(self):
        # 全局样式
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, borderwidth=0)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("TButton", background=BG2, foreground=FG,
                        borderwidth=1, padding=(16, 6))
        style.map("TButton", background=[("active", "#363654")])
        style.configure("TEntry", fieldbackground=BG2, foreground=FG,
                        insertcolor=FG)
        style.configure("TFrame", background=BG)
        style.configure("TLabelframe", background=BG, foreground=FG)
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
        style.configure("TProgressbar", thickness=6)
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])

        # ── 标题栏 ──
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=20, pady=(20, 0))

        ttk.Label(header, text="NCM → MP3 转换器",
                  font=("Microsoft YaHei UI", 18, "bold"),
                  foreground=ACCENT).pack(side="left")
        ttk.Label(header, text="拖入文件，自动转换",
                  font=("Microsoft YaHei UI", 10),
                  foreground="#6c7086").pack(side="left", padx=(12, 0))

        # ── 拖拽区域 ──
        self.drop_frame = tk.Frame(self.root, bg=BG2, bd=0,
                                   highlightthickness=2,
                                   highlightbackground=BORDER)
        self.drop_frame.pack(fill="both", expand=True, padx=20, pady=12)
        self.drop_frame.drop_target_register(DND_FILES)
        self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)
        self.drop_frame.dnd_bind("<<DropEnter>>", self._on_drop_enter)
        self.drop_frame.dnd_bind("<<DropLeave>>", self._on_drop_leave)

        self.drop_label = tk.Label(
            self.drop_frame,
            text="拖拽 .ncm 文件到这里",
            font=("Microsoft YaHei UI", 13),
            bg=BG2, fg="#6c7086",
        )
        self.drop_label.place(relx=0.5, rely=0.45, anchor="center")

        self.btn_select = ttk.Button(
            self.drop_frame, text="选择 NCM 文件",
            command=self._select_files,
        )
        self.btn_select.place(relx=0.5, rely=0.62, anchor="center")

        # ── 进度条 ──
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=20, pady=(0, 4))

        self.status_label = ttk.Label(self.root, text="就绪，等待文件...",
                                      font=("Microsoft YaHei UI", 9))
        self.status_label.pack(anchor="w", padx=20, pady=(0, 2))

        # ── 日志区域 ──
        log_frame = ttk.Labelframe(self.root, text="转换日志", padding=4)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        self.log_text = tk.Text(
            log_frame, height=6, bg=BG2, fg=FG, font=("Consolas", 9),
            state="disabled", wrap="word", bd=0, padx=8, pady=8,
            insertbackground=FG,
        )
        self.log_text.pack(fill="both", expand=True)
        self._setup_log_tags()

        # ── 底部控制栏 ──
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", padx=20, pady=(0, 16))

        # 输出目录
        ttk.Label(bottom, text="输出目录:",
                  font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.dir_entry = ttk.Entry(bottom, textvariable=self.output_dir,
                                   width=28)
        self.dir_entry.pack(side="left", padx=(6, 4))
        self.dir_entry.insert(0, "")  # 空 = 与源文件同目录
        self.dir_entry.bind("<FocusIn>", lambda e: self.dir_entry.select_range(0, "end"))

        ttk.Button(bottom, text="浏览", command=self._browse_dir,
                   padding=(8, 4)).pack(side="left", padx=(2, 16))

        # 封面复选框
        self.cover_cb = ttk.Checkbutton(
            bottom, text="提取封面图", variable=self.extract_cover,
        )
        self.cover_cb.pack(side="left", padx=(0, 16))

        # 清空日志按钮
        ttk.Button(bottom, text="清空日志", command=self._clear_log,
                   padding=(8, 4)).pack(side="right")

    def _setup_log_tags(self):
        self.log_text.tag_config("ok", foreground=GREEN)
        self.log_text.tag_config("err", foreground=RED)
        self.log_text.tag_config("warn", foreground=YELLOW)
        self.log_text.tag_config("info", foreground="#6c7086")

    # ── 拖拽事件 ────────────────────────────────────────────────

    def _on_drop_enter(self, event):
        self.drop_frame.configure(highlightbackground=ACCENT)
        self.drop_label.configure(fg=ACCENT)
        return event.action

    def _on_drop_leave(self, event):
        self.drop_frame.configure(highlightbackground=BORDER)
        self.drop_label.configure(fg="#6c7086")
        return event.action

    def _on_drop(self, event):
        self.drop_frame.configure(highlightbackground=BORDER)
        self.drop_label.configure(fg="#6c7086")

        # tkinterdnd2 传回的路径格式: {file1} {file2} ...
        raw = event.data
        files = self._parse_drop_data(raw)
        if files:
            self._start_conversion(files)
        return event.action

    @staticmethod
    def _parse_drop_data(raw: str) -> list:
        """解析拖拽数据，处理 Windows 路径的各种格式."""
        import re
        # Windows 路径可能被花括号包裹: {E:\path\file.ncm}
        paths = re.findall(r'\{([^}]+)\}', raw)
        if not paths:
            # 试试不带花括号的（空格分隔）
            paths = raw.split()
        # 去掉可能的引号
        clean = []
        for p in paths:
            p = p.strip().strip('"').strip("'")
            if os.path.isfile(p):
                clean.append(p)
        return clean

    # ── 文件选择 ─────────────────────────────────────────────────

    def _select_files(self):
        files = filedialog.askopenfilenames(
            title="选择 NCM 文件",
            filetypes=[("NCM 文件", "*.ncm"), ("所有文件", "*.*")],
        )
        if files:
            self._start_conversion(files)

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.output_dir.set(d)

    # ── 转换核心 ─────────────────────────────────────────────────

    def _start_conversion(self, files: list):
        if self.is_converting:
            messagebox.showwarning("正在转换", "请等待当前任务完成")
            return

        ncm_files = [f for f in files if f.lower().endswith(".ncm")]
        if not ncm_files:
            messagebox.showinfo("提示", "未找到 .ncm 文件")
            return

        self.total_files = len(ncm_files)
        self.done_files = 0
        self.fail_files = 0
        self.progress["maximum"] = self.total_files
        self.progress["value"] = 0

        self._log(f"开始转换 {self.total_files} 个文件", "info")
        self.btn_select.configure(state="disabled")
        self.status_label.configure(text=f"转换中... 0/{self.total_files}")

        # 后台线程处理
        thread = threading.Thread(target=self._worker, args=(ncm_files,),
                                  daemon=True)
        thread.start()

    def _worker(self, ncm_files: list):
        """后台工作线程 —— 逐个转换文件."""
        self.is_converting = True
        raw = self.output_dir.get().strip()
        # 空字符串 或 旧版占位文字 = 与源文件同目录
        out_dir = raw if raw and raw != "与源文件同目录" else None

        for i, file_path in enumerate(ncm_files):
            result = self._convert_one(file_path, out_dir)
            self.convert_queue.put(("progress", {
                "index": i + 1,
                "filename": os.path.basename(file_path),
                "result": result,
            }))

        self.convert_queue.put(("done", None))
        self.is_converting = False

    def _convert_one(self, file_path: str, out_dir: str | None) -> tuple:
        """转换单个文件，返回 (ok: bool, msg: str)."""
        try:
            header = decrypt_ncm_header(file_path)
            audio = decrypt_audio_stream(file_path, header)

            metadata = header["metadata"]
            fmt = detect_format(audio)
            name = generate_filename(metadata, fmt, Path(file_path).stem)

            if out_dir:
                dest_dir = out_dir
            else:
                dest_dir = os.path.dirname(file_path)

            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, name)

            with open(dest_path, "wb") as f:
                f.write(audio)

            # 封面图
            cover_msg = ""
            if self.extract_cover.get() and header.get("cover_data"):
                try:
                    cover_path = os.path.join(dest_dir, Path(name).stem + ".jpg")
                    with open(cover_path, "wb") as f:
                        f.write(header["cover_data"])
                    cover_msg = " + 封面"
                except Exception:
                    pass

            size_mb = len(audio) / (1024 * 1024)
            return True, f"{name} ({size_mb:.1f} MB){cover_msg}"

        except NotNCMFileError:
            return False, "不是有效的 NCM 文件"
        except DecryptionError as e:
            return False, f"解密失败: {e}"
        except Exception as e:
            return False, f"错误: {e}"

    # ── UI 更新（主线程轮询）────────────────────────────────────

    def _poll_queue(self):
        """定时从队列取消息并更新 UI."""
        try:
            while True:
                msg_type, data = self.convert_queue.get_nowait()
                if msg_type == "progress":
                    self._on_progress(data)
                elif msg_type == "done":
                    self._on_done()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_progress(self, data: dict):
        idx = data["index"]
        ok, msg = data["result"]

        if ok:
            self.done_files += 1
            self._log(f"✓ {msg}", "ok")
        else:
            self.fail_files += 1
            self._log(f"✗ {data['filename']}: {msg}", "err")

        self.progress["value"] = idx
        self.status_label.configure(
            text=f"转换中... {self.done_files} 成功, {self.fail_files} 失败"
        )

    def _on_done(self):
        self.btn_select.configure(state="normal")
        self.progress["value"] = self.total_files
        summary = f"完成！成功 {self.done_files}"
        if self.fail_files:
            summary += f"，失败 {self.fail_files}"
        self.status_label.configure(text=summary)
        self._log(summary, "warn" if self.fail_files else "ok")

    # ── 日志 ─────────────────────────────────────────────────────

    def _log(self, text: str, tag: str = ""):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.status_label.configure(text="就绪，等待文件...")

    # ── 启动 ─────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


# ── Entry Point ────────────────────────────────────────────────────

if __name__ == "__main__":
    app = NCMConverterGUI()
    app.run()
