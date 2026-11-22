# 画面PDF化アプリ（Web版）：担当①ブラウザ
# 専用プロファイルの本物のChromeでページを開き、本文を取り出す。突破・回避の仕組みは作らない
import io
import os
import re
import time
from datetime import datetime

from common import PageData, ProtectedPage, StopCapture, is_black, load_config

# 普段使いのChromeのプロファイルは絶対に使わない（この文字列を含む場所は拒否）
_NORMAL_CHROME_MARKERS = ("google\\chrome\\user data", "google/chrome/user data")

# 本文が「極端に短い」と見なす文字数
_MIN_BODY_CHARS = 30
_FALLBACK_NOTE = "（本文を自動判定できませんでした）"


def _check_profile_dir(profile_dir):
    p = os.path.realpath(str(profile_dir)).replace("/", "\\").lower().rstrip("\\") if str(profile_dir) else ""
    if any(m.replace("/", "\\") in p for m in _NORMAL_CHROME_MARKERS) or not p:
        raise StopCapture(
            "普段使いのChromeのプロファイルは使えません。"
            "設定ファイルの「専用Chromeの場所」を、このアプリ専用のフォルダに直してください。"
        )


def _launch(pw, profile_dir, headless):
    """専用プロファイルで本物のChromeを開く"""
    _check_profile_dir(profile_dir)
    kwargs = dict(
        channel="chrome",
        headless=headless,
        locale="ja-JP",
    )
    if headless:
        kwargs["viewport"] = {"width": 1280, "height": 900}
    else:
        kwargs["no_viewport"] = True
    try:
        return pw.chromium.launch_persistent_context(str(profile_dir), **kwargs)
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "chrome" in msg.lower() and "distribution" in msg.lower():
            raise StopCapture(
                "Google Chrome が見つかりません。原因：Chrome が入っていないようです。"
                "Chrome をインストールしてから、もう一度試してください。"
            ) from e
        if "ProcessSingleton" in msg or "user data directory is already in use" in msg or "Target page, context or browser has been closed" in msg:
            raise StopCapture(
                "専用Chromeを開けませんでした。原因：同じ専用Chromeがすでに開いている可能性があります。"
                "開いている専用Chromeのウィンドウを閉じてから、もう一度試してください。"
            ) from e
        raise StopCapture(f"専用Chromeを開けませんでした。原因：{msg[:200]} 専用Chromeを閉じて、もう一度試してください。") from e


# --- 保護の検出 ---------------------------------------------------------
_JS_DETECT = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 50 && r.height > 30 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  // CAPTCHA・確認画面
  const t = (document.title || '');
  if (/just a moment|attention required|verify you are human|ロボットではありません|しばらくお待ちください/i.test(t)
      || document.querySelector('#challenge-form, #cf-challenge-running, #challenge-running, .cf-turnstile')) {
    return 'CAPTCHA または確認画面（Cloudflare など）が表示されています。人の確認が必要なため止めました。ブラウザで確認を済ませてから、もう一度試してください。';
  }
  const frames = [...document.querySelectorAll('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="challenges.cloudflare.com"]')]
    .filter(f => !f.closest('.grecaptcha-badge') && vis(f));
  if (frames.length || [...document.querySelectorAll('.g-recaptcha, .h-captcha')].some(vis)) {
    return 'CAPTCHA（ロボットではない確認）が表示されています。突破はできないため止めました。専用Chromeで確認を済ませてから、もう一度試してください。';
  }
  // コピー禁止
  const content = document.querySelector('article, main, [role=main]') || document.body;
  if (content) {
    const us = (el) => { const s = getComputedStyle(el); return s.userSelect || s.webkitUserSelect; };
    if (us(content) === 'none' || (content !== document.body && us(document.body) === 'none' && us(content) === 'none')) {
      return '本文全体が選択できない設定（user-select:none）です。コピー禁止のページのため止めました。取り込みを許されたページか確認してください。';
    }
    for (const n of ['oncopy', 'onselectstart', 'oncontextmenu']) {
      if (document[n] || document.body[n] || document.documentElement[n]) {
        return 'このページはコピーや選択を禁止する仕組み（' + n + '）を持っています。コピー禁止のため止めました。取り込みを許されたページか確認してください。';
      }
    }
    for (const [type, label] of [['copy', 'コピー'], ['selectstart', '文字の選択'], ['contextmenu', '右クリック']]) {
      const ev = new Event(type, {bubbles: true, cancelable: true});
      content.dispatchEvent(ev);
      if (ev.defaultPrevented) {
        return 'このページは' + label + 'を禁止する仕組みを持っています。コピー禁止のため止めました。取り込みを許されたページか確認してください。';
      }
    }
  }
  // 動画の保護（DRM）
  if ([...document.querySelectorAll('video')].some(v => v.mediaKeys)) {
    return '保護された動画（DRM）があります。突破はできないため止めました。動画以外の本文だけが必要なら、別の方法で取り込んでください。';
  }
  return null;
}
"""


def detect_protection(page):
    """コピー禁止・CAPTCHA・真っ黒な画面を見つけたら理由（日本語）を返す。無ければ None"""
    try:
        reason = page.evaluate(_JS_DETECT)
    except Exception:
        reason = None  # ページ移動中などで調べられないときは、保護なしとして進む
    if reason:
        return reason
    try:
        from PIL import Image
        png = page.screenshot(type="png")
        if is_black(Image.open(io.BytesIO(png))):
            return "画面が真っ黒です。画面の撮影を防ぐ仕組みの可能性があるため止めました。ページが読み込み中でないか確認し、もう一度試してください。"
    except Exception:
        pass
    return None


# --- Cookie同意バナー --------------------------------------------------------
_JS_COOKIE = r"""
() => {
  const word = /^(同意する?|同意して続ける|すべて(を)?(許可|同意|受け入れる?)|全て(許可|同意)|承諾する?|許可する?|ok|okay|accept|accept all|accept all cookies|allow all|allow all cookies|i agree|agree|got it|yes, i agree)$/i;
  const known = ['#onetrust-accept-btn-handler', '#accept-cookies', '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
                 '.cookie-accept', '.cc-allow', '.cc-dismiss'];
  const shown = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const sel of known) {
    const el = document.querySelector(sel);
    if (el && shown(el)) { el.click(); return 'known:' + sel; }
  }
  const hint = /cookie|consent|gdpr|privacy|cmp|banner|同意/i;
  const cands = [...document.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]')];
  for (const el of cands) {
    const txt = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
    if (!txt || txt.length > 30 || !word.test(txt) || !shown(el)) continue;
    // 削除・購入などの確認画面の「OK」は押さない
    const around = ((el.closest('[role=dialog], [role=alertdialog], dialog, form') || el.parentElement || el).innerText || '').slice(0, 400);
    if (/削除|消去|購入|注文|決済|支払|退会|解約|ログアウト|delete|remove|purchase|checkout|payment|logout|unsubscribe/i.test(around)) continue;
    // バナーの中（固定表示、または id/class/aria に cookie 等を含む親）にあるものだけ押す
    let p = el, ok = false;
    for (let i = 0; p && i < 8; i++, p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.position === 'fixed' || s.position === 'sticky' || hint.test((p.id || '') + ' ' + (p.className && p.className.toString() || '') + ' ' + (p.getAttribute('aria-label') || ''))) { ok = true; break; }
    }
    if (ok) { el.click(); return 'text:' + txt; }
  }
  return null;
}
"""

_JS_SCROLL_STEP = """
() => { const h = window.innerHeight; window.scrollBy(0, Math.max(300, Math.floor(h * 0.9)));
  return {y: window.scrollY, h: document.documentElement.scrollHeight, vh: h}; }
"""

# 除外要素を外したHTMLを作る（今のページ自体は変えない）
_JS_CLEAN_HTML = r"""
({include, excludes}) => {
  const doc = document.documentElement.cloneNode(true);
  for (const sel of excludes) {
    try { doc.querySelectorAll(sel).forEach(e => e.remove()); } catch (e) {}
  }
  doc.querySelectorAll('script, style, noscript, template').forEach(e => e.remove());
  const title = document.title || '';
  if (include) {
    let parts = [];
    try { parts = [...doc.querySelectorAll(include)]; } catch (e) {}
    if (parts.length) {
      return {html: '<html><head><title>' + title.replace(/</g, '&lt;') + '</title></head><body>' + parts.map(p => p.outerHTML).join('\n') + '</body></html>', used_include: true};
    }
  }
  return {html: '<!DOCTYPE html>' + doc.outerHTML, used_include: false};
}
"""


class BrowserSession:
    def __init__(self, profile_dir, headless=False):
        self.profile_dir = profile_dir
        self.headless = headless
        self._pw = None
        self._ctx = None
        self.page = None
        self._last_goto = 0.0

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._ctx = _launch(self._pw, self.profile_dir, self.headless)
        except BaseException:
            self._pw.stop()
            self._pw = None
            raise
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *a):
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._pw = self.page = None
        return False

    def goto(self, url):
        """開く。前回の取得から「ページ待ち秒」あけ、読み込みが落ち着くまで待つ"""
        if not str(url).lower().startswith(("http://", "https://")):
            raise StopCapture(
                f"このURLは開けません（{url}）。http:// または https:// で始まるWebページのURLだけ使えます。"
            )
        wait = float(load_config()["ページ待ち秒"])
        gap = time.time() - self._last_goto
        if self._last_goto and gap < wait:
            time.sleep(wait - gap)
        try:
            self.page.goto(url, wait_until="load", timeout=45000)
        except Exception as e:
            raise StopCapture(
                f"ページを開けませんでした（{url}）。原因：{str(e).splitlines()[0][:150]} "
                "URLの間違いやネットワークを確認して、もう一度試してください。"
            ) from e
        finally:
            self._last_goto = time.time()
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # 通信が続くページは、待ち切らずに進む

    def _dismiss_cookie_banner(self):
        for _ in range(2):
            try:
                hit = self.page.evaluate(_JS_COOKIE)
            except Exception:
                hit = None
            if not hit:
                break
            self.page.wait_for_timeout(600)

    def _scroll_all(self, max_sec=25.0):
        """少しずつ最後までスクロールして、遅延読み込みを出す"""
        start = time.time()
        stable = 0
        last_h = 0
        while time.time() - start < max_sec:
            try:
                r = self.page.evaluate(_JS_SCROLL_STEP)
            except Exception:
                break
            self.page.wait_for_timeout(120)
            at_bottom = r["y"] + r["vh"] >= r["h"] - 2
            if at_bottom:
                # 底に着いても、高さが伸びなくなるまで少し待つ
                stable = stable + 1 if r["h"] == last_h else 0
                if stable >= 2:
                    break
            else:
                stable = 0
            last_h = r["h"]
        try:
            self.page.evaluate("window.scrollTo(0, 0)")
            self.page.wait_for_timeout(200)
        except Exception:
            pass

    def prepare(self):
        self._dismiss_cookie_banner()
        self._scroll_all()
        reason = detect_protection(self.page)
        if reason:
            raise ProtectedPage(reason)

    def extract(self, rules=None, with_screenshot=False):
        import trafilatura
        include = getattr(rules, "include_selector", None) if rules else None
        excludes = list(getattr(rules, "exclude_selectors", None) or []) if rules else []
        res = self.page.evaluate(_JS_CLEAN_HTML, {"include": include, "excludes": excludes})
        html = res["html"]
        md = None
        try:
            md = trafilatura.extract(
                html, output_format="markdown", include_tables=True, include_formatting=True,
                include_links=False, favor_recall=True, with_metadata=False,
            )
        except Exception:
            md = None
        md = (md or "").strip()
        if len(md) < _MIN_BODY_CHARS:
            try:
                text = (trafilatura.html2txt(html) or "").strip()
            except Exception:
                text = ""
            md = _FALLBACK_NOTE + "\n\n" + text
        shot = None
        if with_screenshot:
            try:
                shot = self.page.screenshot(full_page=True, type="png")
            except Exception:
                shot = None
        return PageData(
            url=self.page.url, title=(self.page.title() or "").strip(),
            fetched_at=datetime.now(), markdown=md, screenshot_png=shot,
        )


def open_for_login(profile_dir, url, auto_close_sec=None):
    """画面つきで専用Chromeを開き、ウィンドウが閉じられるまで待つ。パスワードは扱わない。
    auto_close_sec は動作確認用（指定秒で自動的に閉じる）"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = _launch(pw, profile_dir, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass  # 開けなくても、利用者が自分でアドレスを入れられる
            try:
                ctx.wait_for_event("close", timeout=int(auto_close_sec * 1000) if auto_close_sec else 0)
            except Exception:
                pass  # 時間切れ（自動で閉じる）
        finally:
            try:
                ctx.close()
            except Exception:
                pass
