# 画面PDF化アプリ（Web版）：全部品が共通で使う土台
# 設定の読み込み・ファイル名・データの形・「止める」例外をここにまとめる
import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from PIL import ImageStat

_G_ROOT = "G:\\マイドライブ"
# Drive の写し（G:\マイドライブ が使えないときの代わり）。
# 既定は「このアプリのフォルダの3つ上」（…\Claude）。別の場所なら環境変数 GAMEN_PDF_DRIVE_MIRROR で指定する。
_C_MIRROR = os.environ.get("GAMEN_PDF_DRIVE_MIRROR") or os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def drive_path(p):
    """Drive の道を、いま使えるものに直す。
    G:\\マイドライブ が使えるときはそのまま。使えないときは、同じ Drive の写し（_C_MIRROR のフォルダ）に読みかえる"""
    p = os.path.expandvars(str(p))
    if p.lower().startswith(_G_ROOT.lower() + "\\") and not os.path.isdir(_G_ROOT):
        return _C_MIRROR + p[len(_G_ROOT):]
    return p


# 設定ファイルの場所（Drive）。中身は設定ファイルで変える
CONFIG_PATH = drive_path(os.environ.get(
    "GAMEN_PDF_CONFIG", r"G:\マイドライブ\画面PDF化アプリ\設定\config.json"
))

DEFAULT_CONFIG = {
    "作成者名": "みち",
    "保存先": r"G:\マイドライブ\画面PDF化アプリ\成果物",
    "サイト設定ファイル": r"G:\マイドライブ\画面PDF化アプリ\設定\サイト設定.json",
    "専用Chromeの場所": r"%LOCALAPPDATA%\画面PDF化アプリ\chrome_profile",  # ログイン情報が入るので Drive には置かない
    "ページ待ち秒": 2.0,
    "最大ページ数": 50,
    "制限時間秒": 600,
    "Markdown分割文字数": 200000,
    "PDFに画面の写真を付ける": False,
    "PDFを作る": True,  # False にすると Markdown だけ作る（ニュースなど文字だけでよいとき）
}


class StopCapture(Exception):
    """取り込みを止める理由（日本語で、原因と直し方を書く）"""


class ProtectedPage(StopCapture):
    """コピー禁止・真っ黒・確認画面（CAPTCHA）を見つけたので止める。突破はしない"""


@dataclass
class PageData:
    """1ページ分の取り込み結果"""
    url: str
    title: str
    fetched_at: datetime
    markdown: str                      # 本文（見出しなどを残した Markdown）
    screenshot_png: bytes | None = None  # 画面全体の写真（任意）


@dataclass
class Document:
    """1回の取り込みの全体（ページ送りした分をまとめたもの）"""
    title: str
    source_url: str                    # 最初のURL
    fetched_at: datetime
    pages: list[PageData] = field(default_factory=list)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    else:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    cfg["専用Chromeの場所"] = os.path.expandvars(cfg["専用Chromeの場所"])
    for key in ("保存先", "サイト設定ファイル"):
        cfg[key] = drive_path(cfg[key])
    return cfg


def next_output_path(folder, title, ext):
    """ファイル名：内容だけ（例：記事の題名.md）。同じ名前があれば「（2）」「（3）」を付ける。
    通し番号・日付・版は付けない（みちの指示 2026-11-03）。ext は '.pdf' や '.md'。
    Markdown と PDF は同じ名前で作る（同じ名前の空きを両方で確かめる）。"""
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c for c in title if c not in '\\/:*?"<>| 　.' and ord(c) >= 32)[:40] or "取り込み"
    n = 1
    while True:
        stem = safe if n == 1 else f"{safe}（{n}）"
        if not any(os.path.exists(os.path.join(folder, stem + e)) for e in (".md", ".pdf")):
            return os.path.join(folder, stem + ext)
        n += 1


def is_black(img):
    """真っ黒な画像（コピー防止などで隠された画面）かどうか"""
    stat = ImageStat.Stat(img.convert("L").resize((200, 200)))
    return stat.mean[0] < 8 and stat.stddev[0] < 4
