# 画面PDF化アプリ（Web版）：部品をつなぐ流れ
# 開く → 準備 → 本文を取り出す → 次を探す → 進む → …を繰り返し、最後に Markdown と PDF を作る
import ctypes
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import output
import paginate
import range_select
import web
from common import Document, StopCapture, load_config

VK_ESCAPE = 0x1B
_STOP = threading.Event()  # 画面を閉じるときなど、プログラム側から止める合図


def request_stop():
    _STOP.set()


class EscWatcher:
    """Esc キーを別スレッドで見張る（短く押しただけでも、止める合図を逃さない）"""

    def __init__(self):
        self._ev = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                if ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                    self._ev.set()
            except Exception:
                pass
            time.sleep(0.03)

    def pressed(self):
        return self._ev.is_set()

    def request_stop(self):
        """画面を閉じるときなど、プログラム側から止める"""
        self._ev.set()

    def close(self):
        self._stop.set()


@dataclass
class Result:
    url: str
    ok: bool
    message: str                       # 日本語の結果・止めた理由
    files: list = field(default_factory=list)
    pages: int = 0


def check_url(url):
    """http / https の Webページだけ許す（ファイルや javascript: などは開かない）"""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise StopCapture(
            f"このURLは使えません（{url[:80]}）。http:// または https:// で始まるWebページのURLを入れてください。"
        )
    return url.strip()


def _host(url):
    h = (urlparse(url).netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


_PAGE_KEYS = {"page", "p", "paged", "pg", "pagenum", "start", "offset", "from", "cursor"}


def _href_of(page, selector):
    """「次へ」の要素のリンク先（絶対URL）。リンクでなければ None"""
    try:
        el = page.query_selector(selector)
        h = el.get_attribute("href") if el else None
        return urljoin(page.url, h) if h else None
    except Exception:
        return None


def looks_like_paging(cur, nxt):
    """cur から nxt への移動が「同じ記事の続きのページ」らしいか（別の記事ではないか）"""
    a, b = urlparse(cur), urlparse(nxt)
    if a.netloc.lower() != b.netloc.lower():
        return False
    qa, qb = parse_qs(a.query), parse_qs(b.query)
    # ページ番号のような値が違う（?page=2 など）
    if a.path == b.path and any(k.lower() in _PAGE_KEYS and qa.get(k) != qb.get(k) for k in set(qa) | set(qb)):
        return True
    # 数字だけが違う（/page/2/、-2.html、_3.html など）
    norm = lambda s: re.sub(r"\d+", "#", s)
    if a.path != b.path and norm(a.path) == norm(b.path):
        return True
    # いまの道の後ろに、ページ番号が付いた形（/article/ → /article/2/ など）
    base = a.path.rstrip("/")
    if b.path.startswith(base + "/") and re.fullmatch(r"/?(page/|p/)?\d+/?", b.path[len(base):]):
        return True
    return False


def _site_rules(cfg, url):
    try:
        return range_select.load_rules(cfg["サイト設定ファイル"], range_select.selector_for_domain(url))
    except ValueError as e:
        raise StopCapture(str(e)) from e  # 直し方つきの全文をそのまま見せる


def capture_url(session, url, cfg, on_progress, esc):
    """1つのURLを、ページ送りも含めて取り込み、Document を返す。止めた理由は doc.stop_reason に入れる。
    途中で止まっても、取れた分は返す（1ページも取れなかったときだけ例外）"""
    url = check_url(url)
    rules = _site_rules(cfg, url)
    hint = rules.next_selector if rules else None
    rule = paginate.StopRule(max_pages=int(cfg["最大ページ数"]), time_limit_sec=float(cfg["制限時間秒"]))
    started = time.time()
    wait = float(cfg["ページ待ち秒"])
    with_shot = bool(cfg["PDFに画面の写真を付ける"])
    start_host = _host(url)

    doc = Document(title="", source_url=url, fetched_at=datetime.now())
    stop_reason = None
    try:
        session.goto(url)
        session.prepare()
        while True:
            # 順番は「本文を取る → 次を探す → 進む」
            page_data = session.extract(rules, with_screenshot=with_shot)
            if doc.pages and paginate.is_duplicate(doc.pages[-1].markdown, page_data.markdown):
                stop_reason = "同じ内容が続いたので、最後のページと判断して止めました。"
                break
            doc.pages.append(page_data)
            if not doc.title:
                doc.title = page_data.title or "取り込み"
            if on_progress:
                on_progress(f"{len(doc.pages)} ページ取り込みました")
            stop_reason = paginate.should_stop(len(doc.pages), started, rule, esc_pressed=esc())
            if stop_reason:
                break
            action = paginate.find_next(session.page, hint)
            if action is None:
                stop_reason = "次のページが見つからないので、ここで終わりました。"
                break
            if action.kind == "link" and not hint and "という文字の" in action.reason:
                # 「Next」などの文字だけで見つけたリンクは、別の記事への移動かもしれない。続きのページらしいときだけたどる
                href = _href_of(session.page, action.selector)
                if href and not looks_like_paging(session.page.url, href):
                    stop_reason = ("「次へ」のリンクは、続きのページではなく別の記事のようなので、たどらずに終わりました。"
                                   "続きのページなら、「「次へ」を教える」で場所を教えてください。")
                    break
            if action.kind == "scroll":
                # 無限スクロール：続きが出なくなるまで送り、最後に1回だけ取り直す（重複させない）
                for _ in range(rule.max_pages):
                    stop_reason = paginate.should_stop(len(doc.pages), started, rule, esc_pressed=esc())
                    if stop_reason or not paginate.follow(session.page, action, wait):
                        break
                doc.pages[-1] = session.extract(rules, with_screenshot=with_shot)
                stop_reason = stop_reason or "下まで読み込んで、ここで終わりました。"
                break
            if not paginate.follow(session.page, action, wait):
                stop_reason = "次へ進んでも内容が変わらないので、ここで終わりました。"
                break
            cur = session.page.url
            if not cur.lower().startswith(("http://", "https://")) or _host(cur) != start_host:
                stop_reason = "別のサイトへ移ろうとしたので、安全のためここで止めました。"
                try:
                    session.page.go_back()
                except Exception:
                    pass
                break
            session.prepare()
    except Exception as e:
        if not doc.pages:
            raise
        reason = str(e) if isinstance(e, StopCapture) else f"途中でエラーが起きました（{type(e).__name__}）"
        stop_reason = f"途中で止まりました。取れた分だけ保存します。理由：{reason}"
    doc.stop_reason = stop_reason
    return doc


def run(urls, on_progress=None, headless=False, esc=None):
    """URL の一覧を順に取り込む。失敗しても次のURLへ進み、結果の一覧を返す"""
    cfg = load_config()
    results = []
    watcher = None
    _STOP.clear()
    if esc is None:
        watcher = EscWatcher()
        esc = lambda: watcher.pressed() or _STOP.is_set()
    try:
        urls = [u.strip() for u in urls if u.strip()]
        with web.BrowserSession(cfg["専用Chromeの場所"], headless=headless) as session:
            for i, url in enumerate(urls, 1):
                say = (lambda m, i=i: on_progress(f"[{i}/{len(urls)}] {m}")) if on_progress else None
                try:
                    if say:
                        say(f"開いています：{url}")
                    doc = capture_url(session, url, cfg, say, esc)
                    files = output.write_outputs(
                        doc, cfg["保存先"], cfg["作成者名"],
                        with_screenshot=bool(cfg["PDFに画面の写真を付ける"]),
                        split_chars=int(cfg["Markdown分割文字数"]),
                        make_pdf=bool(cfg["PDFを作る"]),
                    )
                    msg = f"{len(doc.pages)} ページ取り込みました。{getattr(doc, 'stop_reason', '') or ''}"
                    results.append(Result(url, True, msg, files, len(doc.pages)))
                except StopCapture as e:
                    results.append(Result(url, False, str(e)))
                except Exception as e:
                    traceback.print_exc()
                    results.append(Result(
                        url, False,
                        f"思わぬエラーで止まりました：{type(e).__name__}: {str(e)[:150]}。"
                        "URL・ネット接続・保存先（Drive）を確認して、もう一度試してください。",
                    ))
                if esc():
                    if i < len(urls):
                        results.append(Result("", False, "Esc キーで止めました。残りのURLは取り込んでいません。"))
                    break
    finally:
        if watcher:
            watcher.close()
    return results


def teach(url, kind, headless=False):
    """利用者に、ページ上でクリックで教えてもらう。kind：'本文'（読みたい部分）／'除く'（広告など）／'次へ'
    選んだ結果をサイトごとの設定に保存し、保存した CSS セレクタを返す（やめたときは None）"""
    url = check_url(url)
    cfg = load_config()
    prompts = {
        "本文": "読みたい部分（本文）をクリックしてください（Esc でやめる）",
        "除く": "取り込みたくない部分（広告など）をクリックしてください（Esc でやめる）",
        "次へ": "「次のページ」にあたるボタンやリンクをクリックしてください（Esc でやめる）",
    }
    if kind not in prompts:
        raise StopCapture("教える種類は「本文」「除く」「次へ」のどれかです。")
    with web.BrowserSession(cfg["専用Chromeの場所"], headless=headless) as session:
        session.goto(url)
        session.prepare()
        sel = range_select.pick_element(session.page, prompts[kind])
        if not sel:
            return None
        domain = range_select.selector_for_domain(url)
        try:
            rules = range_select.load_rules(cfg["サイト設定ファイル"], domain)
        except ValueError as e:
            raise StopCapture(str(e)) from e
        rules = rules or range_select.SiteRules(domain=domain)
        if kind == "本文":
            rules.include_selector = sel
        elif kind == "除く":
            if sel not in rules.exclude_selectors:
                rules.exclude_selectors.append(sel)
        else:
            rules.next_selector = sel
        range_select.save_rules(cfg["サイト設定ファイル"], rules)
        return sel


def login(url):
    """専用Chromeを画面つきで開く。ウィンドウを閉じるまで待つ（ログインを覚えさせる）"""
    url = check_url(url)
    cfg = load_config()
    web.open_for_login(cfg["専用Chromeの場所"], url)
