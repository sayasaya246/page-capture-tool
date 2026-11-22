# 画面PDF化アプリ（Web版）：画面（ウィンドウ）の部分
import ctypes
import os
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from tkinter import messagebox, ttk

import pipeline
from common import StopCapture, load_config

AI_SITES = {
    "NotebookLM": "https://notebooklm.google.com/",
    "Gemini": "https://gemini.google.com/",
    "ChatGPT": "https://chatgpt.com/",
}

# --- 色（決定済みのデザイン） ---
PAGE = "#FAF6EF"
BAND = "#E4EDE0"
ACCENT = "#B5502A"
ACCENT_HOVER = "#9E4322"
CARD = "#FFFFFF"
CARD_EDGE = "#E6DCCB"
TEXT = "#33302C"
SUB = "#665F57"
STOP = "#9B2F2F"
GREEN = "#47705A"
TROUGH = "#E3E8DF"
SETTING_BG = "#F3EBDD"
BTN_EDGE = "#D8CBB5"
AI_EDGE = "#BFD0C2"
HOVER_GREEN = "#EDF2EA"
OK_BG = "#E8F3EC"
OK_TEXT = "#2B724A"
NG_BG = "#FBEBE9"
NG_TEXT = "#A3282B"

# --- 書体・拡大率（起動時に決める） ---
FONT = "Yu Gothic UI"   # なければ Meiryo UI（App.__init__ で切り替え）
K = 1.0                 # 拡大率（96dpi のとき 1.0）


def px(n):
    """直書きの px を、画面の拡大率に合わせた値にする"""
    return int(round(n * K))


def F(size, *style):
    """書体の指定（Yu Gothic UI など）"""
    return (FONT, size, *style)


WAIT_TEXT = "URLを入れて、「取り込みをはじめる」を押してください。"
WORK_TEXT = "ページを読んでいます。ゆっくりお待ちください。\n止めるときは「■ 止める」を押します（Esc キーの長押しでも止まります）。"
DONE_TEXT = "できました。下の結果を見てください。"
FAIL_TEXT = "うまくいきませんでした。理由は下に書いてあります。"
FAIL_HINT = "失敗したページは、「本文を教える」を試してください。ページの本文の場所を1回教えると、次からそのサイトで自動で使います。"
SHORT_HINT = "下の「うまくいかない時の設定」の「本文を教える」を試してください。"


def mix(a, b, t):
    """色 a と b を t（0〜1）の割合で混ぜる（押せない時の薄い色に使う）"""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def _rrect(c, x1, y1, x2, y2, r, **kw):
    """Canvas に角丸の四角を描く"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
           x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class RoundedFrame(tk.Canvas):
    """角丸の面（カード・状態欄・設定欄）。中身は self.body に置く。
    edge を指定すると細い枠線が付く。auto_width=True は中身の幅に合わせる。"""

    def __init__(self, parent, fill=CARD, edge=None, bg=PAGE, radius=16, pad=14, auto_width=False):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self.fill = fill
        self.edge = edge
        self.radius = px(radius)
        self.pad = px(pad)
        self.auto_width = auto_width
        self.body = tk.Frame(self, bg=fill)
        self.body.columnconfigure(0, weight=1)
        self.win = self.create_window(self.pad, self.pad, window=self.body, anchor="nw")
        self.bind("<Configure>", lambda e: self._redraw())
        self.body.bind("<Configure>", self._on_body)

    def _on_body(self, e):
        if self.auto_width:
            self.config(width=self.body.winfo_reqwidth() + 2 * self.pad)
        self.config(height=self.body.winfo_reqheight() + 2 * self.pad)

    def _redraw(self):
        self.delete("bg")  # 中身の窓（window）は消さない
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        if not self.auto_width:
            self.itemconfigure(self.win, width=max(1, w - 2 * self.pad))
        if self.edge:
            _rrect(self, 0, 0, w - 1, h - 1, self.radius, fill=self.edge, tags="bg")
            _rrect(self, 1, 1, w - 2, h - 2, max(self.radius - 1, 0), fill=self.fill, tags="bg")
        else:
            _rrect(self, 0, 0, w - 1, h - 1, self.radius, fill=self.fill, tags="bg")
        self.tag_lower("bg")  # 背景を中身の下に回す

    def set_fill(self, fill):
        self.fill = fill
        self.body.config(bg=fill)
        self._redraw()


class RoundedButton(tk.Canvas):
    """角丸のボタン。押せない時は薄くして、クリックとキーを無視する。
    Tab で移動、Enter かスペースで押せる。kind は normal / ai / primary / stop。"""
    PALETTE = {
        "normal": {"fill": CARD, "edge": BTN_EDGE, "text": TEXT, "hover": HOVER_GREEN},
        "ai": {"fill": CARD, "edge": AI_EDGE, "text": TEXT, "hover": HOVER_GREEN},
        "primary": {"fill": ACCENT, "edge": ACCENT, "text": "#FFFFFF", "hover": ACCENT_HOVER},
        "stop": {"fill": STOP, "edge": STOP, "text": "#FFFFFF", "hover": "#7E2525"},
    }

    def __init__(self, parent, text, command=None, kind="normal", pill=False, minh=48,
                 font=None, bg=PAGE, auto=False, padx=18):
        self.text = text
        self.command = command
        self.kind = kind
        self.pill = pill
        self.auto = auto
        self.padx = px(padx)
        self.minh = px(minh)
        self._font = font or F(11)
        self.radius = px(12)
        self.enabled = True
        self.hover = False
        self.pressed = False
        self.focused = False
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=self.minh,
                         takefocus=1, cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        if self.auto:
            self._fit()

    def _fit(self):
        w = tkfont.Font(font=self._font).measure(self.text) + 2 * self.padx
        super().config(width=max(w, px(96)))

    def config(self, cnf=None, **kw):
        if cnf:
            kw = {**cnf, **kw}
        if "text" in kw:
            self.text = kw.pop("text")
            if self.auto:
                self._fit()
        if "command" in kw:
            self.command = kw.pop("command")
        if "kind" in kw:
            self.kind = kw.pop("kind")
        if "state" in kw:
            self.enabled = kw.pop("state") != "disabled"
            kw["cursor"] = "hand2" if self.enabled else "arrow"
        if kw:
            super().config(**kw)
        self._draw()

    configure = config

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        p = self.PALETTE[self.kind]
        fill, edge, txt = p["fill"], p["edge"], p["text"]
        if not self.enabled:
            fill = mix(fill, PAGE, 0.5)
            edge = mix(edge, PAGE, 0.5)
            txt = mix(txt, PAGE, 0.45)
        elif self.pressed or self.hover:
            fill = p["hover"]
            if self.kind in ("primary", "stop"):
                edge = p["hover"]
        r = h // 2 if self.pill else self.radius
        if self.focused and self.enabled:
            _rrect(self, 0, 0, w - 1, h - 1, r, fill=GREEN)
            _rrect(self, 3, 3, w - 4, h - 4, max(r - 3, 0), fill=fill)
        else:
            _rrect(self, 0, 0, w - 1, h - 1, r, fill=edge)
            _rrect(self, 1, 1, w - 2, h - 2, max(r - 1, 0), fill=fill)
        self.create_text(w // 2, h // 2, text=self.text, fill=txt, font=self._font)

    def _on_enter(self, e):
        if self.enabled:
            self.hover = True
            self._draw()

    def _on_leave(self, e):
        self.hover = False
        self.pressed = False
        self._draw()

    def _on_press(self, e):
        if not self.enabled:
            return
        self.focus_set()
        self.pressed = True
        self._draw()

    def _on_release(self, e):
        if not self.enabled:
            return
        was = self.pressed
        self.pressed = False
        self._draw()
        if was and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.invoke()

    def _on_focus_in(self, e):
        self.focused = True
        self._draw()

    def _on_focus_out(self, e):
        self.focused = False
        self._draw()


class RoundedProgress(tk.Canvas):
    """細い角丸の進み具合バー。作業中だけ start() で動く。"""

    def __init__(self, parent, bg=CARD):
        super().__init__(parent, height=px(8), bg=bg, highlightthickness=0, bd=0)
        self.pos = 0.0
        self.job = None
        self.running = False
        self.bind("<Configure>", lambda e: self._draw())

    def start(self, interval=15):
        self.running = True
        self._tick()

    def stop(self):
        self.running = False
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self._draw()

    def _tick(self):
        self.job = None
        if not self.running:
            return
        self.pos = (self.pos + 0.02) % 1.0
        self._draw()
        self.job = self.after(40, self._tick)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        _rrect(self, 0, 0, w - 1, h - 1, h // 2, fill=TROUGH)
        if self.running:
            seg = w * 0.25
            x = self.pos * (w + seg) - seg
            x1, x2 = max(0, x), min(w - 1, x + seg)
            if x2 > x1 + h:
                _rrect(self, x1, 0, x2, h - 1, h // 2, fill=GREEN)


class StepHead(tk.Frame):
    """緑の丸番号と見出し（「1」「2」「3」。3 は「!」の赤にもできる）"""

    def __init__(self, parent, number, text, bg=PAGE):
        super().__init__(parent, bg=bg)
        d = px(30)
        self.dot = tk.Canvas(self, width=d, height=d, bg=bg, highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=0)
        self.label = tk.Label(self, text=text, font=F(13, "bold"), bg=bg, fg=TEXT)
        self.label.grid(row=0, column=1, sticky="w", padx=(px(10), 0))
        self.set(number, GREEN)

    def set(self, number, color, text=None):
        d = px(30)
        self.dot.delete("all")
        self.dot.create_oval(1, 1, d - 2, d - 2, fill=color, outline=color)
        self.dot.create_text(d // 2, d // 2, text=number, fill="#FFFFFF", font=F(12, "bold"))
        if text is not None:
            self.label.config(text=text)


class App:
    def __init__(self):
        global FONT, K
        try:  # 高DPI（拡大表示）でぼやけないように。失敗しても続ける
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        K = self.root.winfo_fpixels("1i") / 96.0
        if "Yu Gothic UI" not in set(tkfont.families(self.root)) and "Meiryo UI" in set(tkfont.families(self.root)):
            FONT = "Meiryo UI"
        self.root.title("画面PDF化アプリ")
        self.root.resizable(False, True)
        self.busy = False
        self.mode = "start"        # start（取り込み）／teach（本文などを教える）／login（ログイン用）
        self.idle_text = WAIT_TEXT  # 作業していないときの状態メッセージ
        self.msg = ""               # 進み具合・お知らせ（pipeline からの言葉もここに出す）
        self.toast_job = None
        self.tick_job = None
        self.t0 = 0.0
        self.cfg = None
        self.config_error = None
        try:
            self.cfg = load_config()
        except Exception as e:
            self.config_error = f"設定を読めませんでした：{e}\nDrive（G:）がつながっているか確認してください。"
        self.build()
        if self.config_error:
            self.msg = self.config_error
            self.show_status()

    def build(self):
        self.root.configure(bg=PAGE)
        self.root.minsize(px(520), px(360))
        self.status = tk.StringVar(value=WAIT_TEXT)
        self.notice = tk.StringVar(value="")
        self.elapsed = tk.StringVar(value="")
        self.toast = tk.StringVar(value="")

        # 全体を縦にスクロールできる入れ物（小さい画面でも「取り込みをはじめる」が見えるように）
        outer = tk.Frame(self.root, bg=PAGE)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=PAGE, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=PAGE)
        self.inner.columnconfigure(0, weight=1)
        self.inner_win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.inner_win, width=e.width))
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind_all("<Button-4>", self._on_wheel)
        self.root.bind_all("<Button-5>", self._on_wheel)

        # 1. 題名の帯
        band = RoundedFrame(self.inner, fill=BAND, radius=16, pad=16)
        band.grid(row=0, column=0, sticky="ew", padx=px(16), pady=(px(16), px(8)))
        tk.Frame(band.body, bg=ACCENT, width=px(4)).grid(row=0, column=0, sticky="ns", padx=(0, px(12)))
        tb = tk.Frame(band.body, bg=BAND)
        tb.grid(row=0, column=1, sticky="w")
        band.body.columnconfigure(0, weight=0)  # 既定では0列目が広がるので戻す
        band.body.columnconfigure(2, weight=1)  # 右に余白の列を作り、中身を左に寄せる
        tk.Label(tb, text="画面PDF化アプリ", font=F(18, "bold"), bg=BAND, fg=TEXT).pack(anchor="w")
        tk.Label(tb, text="気になるページを、読みやすいPDFにします。", font=F(9), bg=BAND, fg=SUB).pack(anchor="w")

        # 2. 1：URLを入れる
        s1 = tk.Frame(self.inner, bg=PAGE)
        s1.grid(row=1, column=0, sticky="ew", padx=px(16), pady=(px(12), 0))
        s1.columnconfigure(0, weight=1)
        StepHead(s1, "1", "読みたいページのURLを入れます").grid(row=0, column=0, sticky="w")
        wrap = tk.Frame(s1, bg=PAGE)
        wrap.grid(row=1, column=0, sticky="ew", pady=(px(14), 0))
        url_box = RoundedFrame(wrap, fill=CARD, edge=CARD_EDGE, radius=16, pad=8)
        url_box.pack(fill="x", pady=(px(10), 0))  # 上に余白を空け、見出しが切れないようにする
        self.urls = tk.Text(url_box.body, height=2, width=60, wrap="char", bd=0, relief="flat",
                            highlightthickness=0, font=F(11), bg=CARD, fg=TEXT, insertbackground=TEXT)
        self.urls.pack(fill="x", padx=px(8), pady=px(6))
        # 枠の上に重ねる見出し「ページのURL」
        url_lbl = tk.Label(wrap, text="ページのURL", font=F(9), bg=PAGE, fg=SUB)
        url_lbl.place(x=px(16), y=0)
        url_lbl.lift()
        tk.Label(s1, text="いくつもあるときは、1行に1つずつ入れてください。", font=F(9), bg=PAGE, fg=SUB).grid(
            row=2, column=0, sticky="w", pady=(px(6), 0))

        # 3. 2：取り込みます
        s2 = tk.Frame(self.inner, bg=PAGE)
        s2.grid(row=2, column=0, sticky="ew", padx=px(16), pady=(px(18), 0))
        s2.columnconfigure(0, weight=1)
        StepHead(s2, "2", "取り込みます").grid(row=0, column=0, sticky="w")
        self.main_btn = RoundedButton(s2, text="▶ 取り込みをはじめる", kind="primary", minh=60,
                                      font=F(14, "bold"), command=self.on_main_button)
        self.main_btn.grid(row=1, column=0, sticky="ew", pady=(px(12), 0))
        self.status_box = RoundedFrame(s2, fill=CARD, edge=CARD_EDGE, radius=16, pad=14)
        self.status_box.grid(row=2, column=0, sticky="ew", pady=(px(12), 0))
        sb_body = self.status_box.body
        self.status_lbl = tk.Label(sb_body, textvariable=self.status, wraplength=px(600), justify="left",
                                   font=F(11), bg=CARD, fg=TEXT)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        self.bar = RoundedProgress(sb_body, bg=CARD)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(px(8), 0))
        self.bar.grid_remove()
        self.elapsed_lbl = tk.Label(sb_body, textvariable=self.elapsed, font=F(9), bg=CARD, fg=SUB)
        self.elapsed_lbl.grid(row=2, column=0, sticky="w", pady=(px(4), 0))
        self.notice_lbl = tk.Label(sb_body, textvariable=self.notice, wraplength=px(600), justify="left",
                                   font=F(9), bg=CARD, fg=SUB)
        self.notice_lbl.grid(row=3, column=0, sticky="w")

        # 4. 3：できあがり（完了・失敗のときだけ出す）
        self.result_box = tk.Frame(self.inner, bg=PAGE)
        self.result_box.grid(row=3, column=0, sticky="ew", padx=px(16), pady=(px(18), 0))
        self.result_box.columnconfigure(0, weight=1)
        self.step3 = StepHead(self.result_box, "3", "できあがり")
        self.step3.grid(row=0, column=0, sticky="w")
        self.result = RoundedFrame(self.result_box, fill=CARD, edge=CARD_EDGE, radius=16, pad=14)
        self.result.grid(row=1, column=0, sticky="ew", pady=(px(12), 0))
        rb = self.result.body
        self.result_text = tk.StringVar(value="")
        tk.Label(rb, textvariable=self.result_text, wraplength=px(600), justify="left",
                 font=F(11), bg=CARD, fg=TEXT).grid(row=0, column=0, sticky="w")
        self.fail_text = tk.StringVar(value="")
        self.fail_lbl = tk.Label(rb, textvariable=self.fail_text, wraplength=px(600), justify="left",
                                 font=F(11), bg=CARD, fg=NG_TEXT)
        self.fail_lbl.grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        self.hint_text = tk.StringVar(value=FAIL_HINT)
        self.hint = tk.Label(rb, textvariable=self.hint_text, wraplength=px(600), justify="left",
                             font=F(9), bg=CARD, fg=SUB)
        self.hint.grid(row=2, column=0, sticky="w", pady=(px(4), 0))
        self.ai_row = tk.Frame(rb, bg=CARD)
        self.ai_row.grid(row=3, column=0, sticky="w", pady=(px(12), 0))
        tk.Label(self.ai_row, text="できたファイルをAIに読ませる", font=F(11, "bold"),
                 bg=CARD, fg=TEXT).pack(anchor="w")
        ai_btns = tk.Frame(self.ai_row, bg=CARD)
        ai_btns.pack(anchor="w", pady=(px(6), 0))
        for name, url in AI_SITES.items():
            RoundedButton(ai_btns, text=name, kind="ai", pill=True, auto=True, bg=CARD,
                          command=lambda u=url: webbrowser.open(u)).pack(side="left", padx=(0, px(6)))
        self.folder_btn = RoundedButton(rb, text="保存先フォルダを開く", kind="normal", pill=True,
                                        auto=True, bg=CARD, command=self.open_folder)
        self.folder_btn.grid(row=4, column=0, sticky="w", pady=(px(12), 0))
        self.result_box.grid_remove()

        # 5. 初回だけ・うまくいかない時の設定（作業中は押せない）
        self.setting_box = RoundedFrame(self.inner, fill=SETTING_BG, radius=16, pad=16)
        self.setting_box.grid(row=4, column=0, sticky="ew", padx=px(16), pady=(px(22), px(8)))
        sbd = self.setting_box.body
        sbd.columnconfigure(0, weight=1)
        tk.Label(sbd, text="初回だけ・うまくいかない時の設定", font=F(13, "bold"),
                 bg=SETTING_BG, fg=TEXT).grid(row=0, column=0, sticky="w")
        tk.Label(sbd, text="ログインが必要なサイトのとき", font=F(11, "bold"),
                 bg=SETTING_BG, fg=TEXT).grid(row=1, column=0, sticky="w", pady=(px(10), 0))
        tk.Label(sbd, text="ログインが必要なページを読むときは、先にここで専用ブラウザにログインします。"
                           "一度ログインすれば、次からは同じ専用ブラウザで読めます。",
                 wraplength=px(600), justify="left", font=F(9), bg=SETTING_BG, fg=SUB).grid(
            row=2, column=0, sticky="w", pady=(px(2), 0))
        login_btn = RoundedButton(sbd, text="専用ブラウザを開く（ログイン用）", kind="normal", auto=True,
                                  bg=SETTING_BG, command=self.login)
        login_btn.grid(row=3, column=0, sticky="w", pady=(px(6), 0))
        tk.Label(sbd, text="読み取りがずれたとき", font=F(11, "bold"),
                 bg=SETTING_BG, fg=TEXT).grid(row=4, column=0, sticky="w", pady=(px(10), 0))
        tk.Label(sbd, text="ページの本文がうまく取れないときに、場所を1回教えます。教えたサイトでは、次から自動で使います。",
                 wraplength=px(600), justify="left", font=F(9), bg=SETTING_BG, fg=SUB).grid(
            row=5, column=0, sticky="w", pady=(px(2), 0))
        tf = tk.Frame(sbd, bg=SETTING_BG)
        tf.grid(row=6, column=0, sticky="w", pady=(px(6), 0))
        self.setting_btns = [login_btn]
        for text, kind in (("本文を教える", "本文"), ("広告などを除く", "除く"), ("「次へ」を教える", "次へ")):
            b = RoundedButton(tf, text=text, kind="normal", auto=True, bg=SETTING_BG,
                              command=lambda k=kind: self.teach(k))
            b.pack(side="left", padx=(0, px(6)))
            self.setting_btns.append(b)

        # 下の余白（「保存しました」の帯が、ボタンに重ならないための空き）
        tk.Frame(self.inner, bg=PAGE, height=px(64)).grid(row=5, column=0, sticky="ew")

        # 6. 保存したときの小さい帯（レイアウトを押し下げないよう、重ねて出す）
        self.toast_box = RoundedFrame(self.root, fill=OK_BG, radius=14, pad=10, auto_width=True, bg=PAGE)
        tk.Label(self.toast_box.body, textvariable=self.toast, font=F(11, "bold"),
                 bg=OK_BG, fg=OK_TEXT).grid(row=0, column=0)

        # 初めの大きさ：中身の高さ。上限は画面の高さの80%
        self.show_status()  # 空の行を消してから、高さを測る
        self.root.update_idletasks()
        want = self.inner.winfo_reqheight() + px(8)
        sh = self.root.winfo_screenheight()
        h = min(want, int(sh * 0.8))  # タスクバーとタイトルバーの分を残す
        x = max(0, (self.root.winfo_screenwidth() - px(720)) // 2)
        y = max(0, int(sh * 0.04))    # 画面の上のほうに置く（下にはみ出さない）
        self.root.geometry(f"{px(720)}x{h}+{x}+{y}")

    def _on_wheel(self, e):
        if isinstance(e.widget, tk.Text):  # 本文欄の中では、欄の中だけ動かす
            return
        if getattr(e, "num", None) == 4:
            d = -1
        elif getattr(e, "num", None) == 5:
            d = 1
        else:
            d = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(d, "units")

    # --- 共通 ---
    def say(self, text):
        self.msg = text
        self.root.after(0, self.show_status)

    def show_status(self):
        if self.busy:
            text, fill, fg = WORK_TEXT, CARD, TEXT
            self.elapsed.set(f"経過 {int(time.time() - self.t0) // 60:02d}:{int(time.time() - self.t0) % 60:02d}")
        else:
            self.elapsed.set("")
            if self.idle_text == FAIL_TEXT:
                text, fill, fg = FAIL_TEXT, NG_BG, NG_TEXT
            elif self.idle_text == DONE_TEXT:
                text, fill, fg = DONE_TEXT, OK_BG, OK_TEXT
            else:
                text, fill, fg = self.idle_text, CARD, TEXT
        self.status.set(text)
        self.status_box.set_fill(fill)
        for lbl in (self.status_lbl, self.elapsed_lbl, self.notice_lbl):
            lbl.config(bg=fill)
        self.status_lbl.config(fg=fg)
        self.bar.config(bg=fill)
        self.notice.set(self.msg)
        # 空の行は消して、状態欄に余計な空きができないようにする
        for lbl, var in ((self.elapsed_lbl, self.elapsed), (self.notice_lbl, self.notice)):
            if var.get():
                lbl.grid()
            else:
                lbl.grid_remove()

    def _tick(self):
        self.tick_job = None
        if not self.busy:
            return
        self.show_status()
        self.tick_job = self.root.after(1000, self._tick)

    def url_list(self):
        return [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]

    def one_url(self):
        urls = self.url_list()
        if len(urls) != 1:
            self.say("URLを1つだけ入れてから押してください。")
            return None
        return urls[0]

    def _toast_set(self, text):
        self.toast.set(text)
        if text:
            self.toast_box.place(relx=0.5, rely=1.0, anchor="s", y=-px(14))
        else:
            self.toast_box.place_forget()

    def hide_result(self):
        self.result_box.grid_remove()
        self._toast_set("")

    def flash(self, text):
        """保存したことを短く出して、数秒で消す"""
        self._toast_set(text)
        if self.toast_job:
            self.root.after_cancel(self.toast_job)
        self.toast_job = self.root.after(4000, lambda: self._toast_set(""))

    def show_done(self, text, has_fail, saved, head=None, fail=""):
        self.msg = ""
        failed_only = bool(has_fail and not saved)
        self.idle_text = FAIL_TEXT if failed_only else DONE_TEXT
        if head is None:
            head = "できなかった理由" if failed_only else f"できあがり：{saved}件できました"
        self.step3.set("!" if failed_only else "3", NG_TEXT if failed_only else GREEN, head)
        self.result_text.set(text)
        self.fail_text.set(fail)
        if fail:
            self.fail_lbl.grid()
        else:
            self.fail_lbl.grid_remove()
        self.hint_text.set(FAIL_HINT if failed_only else SHORT_HINT)
        if has_fail:
            self.hint.grid()
        else:
            self.hint.grid_remove()
        if failed_only:
            self.ai_row.grid_remove()
        else:
            self.ai_row.grid()
        self.result_box.grid()
        self.show_status()
        if saved:
            self.flash(f"保存しました（{saved}件）")

    def show_result(self, results):
        ok = [r for r in results if r.ok and r.url]
        ng = [r for r in results if not r.ok]
        lines_ok = "\n".join(
            f"・{r.url}\n  → {os.path.basename(r.files[0]) if r.files else ''}（{r.pages}ページ）" for r in ok)
        lines_ng = "\n".join(f"・{r.url or '（全体）'}\n  × {r.message}" for r in ng)
        if ok:
            head = f"できあがり：{len(ok)}件できました（うまくいかなかったもの {len(ng)}件）"
            self.show_done(lines_ok, has_fail=bool(ng), saved=len(ok), head=head, fail=lines_ng)
        else:
            self.show_done(lines_ng or "（できたものはありません）", has_fail=bool(ng), saved=0)

    def show_error(self, msg):
        if self.mode == "start":
            self.show_done(f"止まりました。\n{msg}", has_fail=True, saved=0)
        else:
            self.say(msg)

    def set_busy(self, busy):
        def ui():
            self.busy = busy
            if busy:
                self.main_btn.config(text="■ 止める", state="normal", kind="stop")
            else:
                self.main_btn.config(text="▶ 取り込みをはじめる", state="normal", kind="primary")
            self.urls.config(state="disabled" if busy else "normal")
            for w in self.setting_btns:
                w.config(state="disabled" if busy else "normal")
            if busy:
                self.t0 = time.time()
                self.bar.grid()
                self.bar.start(15)
                if self.tick_job is None:
                    self._tick()
            else:
                self.bar.stop()
                self.bar.grid_remove()
                if self.tick_job:
                    self.root.after_cancel(self.tick_job)
                    self.tick_job = None
            self.show_status()
        self.root.after(0, ui)

    def on_main_button(self):
        if self.busy:
            self.stop()
        else:
            self.start()

    def stop(self):
        self.say("止めています…")
        self.main_btn.config(state="disabled")
        pipeline.request_stop()

    def open_folder(self):
        if not self.cfg:
            self.say("設定を読めていないため、保存先を開けません。")
            return
        os.makedirs(self.cfg["保存先"], exist_ok=True)
        os.startfile(self.cfg["保存先"])

    def run_bg(self, work, done):
        if self.busy:
            return
        if not self.cfg:
            self.say(self.config_error or "設定を読めていません。Drive（G:）がつながっているか確認して、アプリを開き直してください。")
            return
        self.set_busy(True)

        def target():
            try:
                done(work())
            except StopCapture as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: self.show_error(m))
            except Exception as e:
                traceback.print_exc()
                msg = f"思わぬエラーで止まりました：{e}\n設定やネット接続を確認して、もう一度試してください。"
                self.root.after(0, lambda m=msg: self.show_error(m))
            finally:
                self.set_busy(False)
        threading.Thread(target=target, daemon=True).start()

    # --- 操作 ---
    def start(self):
        urls = self.url_list()
        if not urls:
            self.say("先に取り込みたいページのURLを入れてください。")
            return
        self.mode = "start"
        self.idle_text = WAIT_TEXT
        self.hide_result()
        self.say("専用ブラウザを開いています…")

        def done(results):
            self.root.after(0, lambda: self.show_result(results))

        self.run_bg(lambda: pipeline.run(urls, on_progress=self.say), done)

    def teach(self, kind):
        url = self.one_url()
        if not url:
            return
        self.mode = "teach"
        self.idle_text = WAIT_TEXT
        self.hide_result()
        self.say(f"専用ブラウザを開いています。ページ上で「{kind}」の場所をクリックしてください。")

        def done(sel):
            self.say(f"「{kind}」を覚えました（{sel}）。次からこのサイトで自動で使います。" if sel
                     else "やめました。何も保存していません。")
        self.run_bg(lambda: pipeline.teach(url, kind), done)

    def login(self):
        urls = self.url_list()
        url = urls[0] if urls else "https://www.google.com/"
        self.mode = "login"
        self.idle_text = WAIT_TEXT
        self.hide_result()
        self.say("専用ブラウザを開きました。ログインしたら、そのウィンドウを閉じてください。")

        def done(_):
            self.say("専用ブラウザを閉じました。ログインは覚えています。")
        self.run_bg(lambda: pipeline.login(url), done)

    def on_close(self):
        # 取り込み中に閉じても、専用ブラウザが残らないよう、止めてから閉じる
        if self.busy:
            if not messagebox.askyesno("画面PDF化アプリ", "作業中です。止めて閉じますか？\n（取れた分は保存されます）"):
                return
            pipeline.request_stop()
            for _ in range(150):  # 最大15秒、後片付けを待つ
                self.root.update()
                if not self.busy:
                    break
                time.sleep(0.1)
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
