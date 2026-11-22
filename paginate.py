# 画面PDF化アプリ（Web版）：ページ送り（「次へ」を探して進む）
# Playwright の同期 Page だけを使う。危ない操作（購入・削除・ログアウト・送信など）は押さない
import difflib
import time
import unicodedata
from dataclasses import dataclass

# 押してはいけない文言（テキスト・href・aria-label・id・class などに含まれていたら候補から除く）
DANGER_WORDS = [
    "購入", "注文", "決済", "削除", "消去", "退会", "解約", "ログアウト", "サインアウト", "送信", "投稿", "支払",
    "checkout", "delete", "remove", "logout", "log-out", "log out", "signout", "sign-out", "sign out",
    "purchase", "buy-now", "buynow", "submit", "unsubscribe", "payment", "add-to-cart", "addtocart", "order-now",
    "確定", "申込", "申し込み", "予約", "登録", "変更", "cancel", "confirm",
]

# 「次へ」を表す文字（短い文字列に含まれていれば候補）
NEXT_WORDS = [
    "次へ", "次のページ", "次ページ", "次の", "next", "もっと見る", "もっと読む", "もっと表示", "さらに表示", "さらに読み込",
    "続きを読む", "続きを見る", "load more", "show more", "view more", "›", "»", "→",
]
NEXT_EXACT = ["次", ">", ">>", "＞", "》", "more", "next"]
PREV_WORDS = ["前へ", "前の", "前ページ", "prev", "previous", "back", "戻る", "‹", "«", "←"]

# ブラウザ側で動かす部品（要素の探索・判定・セレクタ作成）
_JS_LIB = r"""
(() => {
  if (window.__gp) return;
  const DANGER = %DANGER%;
  const NEXTW = %NEXTW%;
  const NEXTX = %NEXTX%;
  const PREVW = %PREVW%;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const low = s => norm(s).toLowerCase();
  const visible = el => {
    if (!el.isConnected) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const disabled = el => {
    for (let e = el, i = 0; e && i < 3; e = e.parentElement, i++) {
      if (e.disabled || e.getAttribute('aria-disabled') === 'true' || e.hasAttribute('inert')) return true;
      const c = ' ' + (e.className && e.className.baseVal === undefined ? e.className : '') + ' ';
      if (/\b(disabled|is-disabled|inactive|unavailable)\b/i.test(c)) return true;
      if (getComputedStyle(e).pointerEvents === 'none') return true;
    }
    return false;
  };
  const labelOf = el => [el.innerText || el.textContent, el.getAttribute('aria-label'), el.getAttribute('title'),
      el.getAttribute('alt'), el.value && typeof el.value === 'string' ? el.value : ''].map(norm).filter(Boolean);
  const danger = el => {
    const parts = labelOf(el).concat([el.getAttribute('href'), el.id, typeof el.className === 'string' ? el.className : '',
      el.getAttribute('name'), el.getAttribute('onclick'), el.getAttribute('formaction'), el.getAttribute('type')]);
    const s = low(parts.filter(Boolean).join(' '));
    if (DANGER.some(w => s.includes(w))) return true;
    if (el.tagName === 'INPUT' && (el.type === 'submit')) return true;
    if (el.tagName === 'BUTTON' && el.type === 'submit' && el.form) return true;
    return false;
  };
  const cssPath = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.documentElement; e = e.parentElement) {
      if (e.id && document.querySelectorAll('#' + CSS.escape(e.id)).length === 1) {
        parts.unshift('#' + CSS.escape(e.id)); break;
      }
      let n = 1;
      for (let s = e.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === e.tagName) n++;
      parts.unshift(e.tagName.toLowerCase() + ':nth-of-type(' + n + ')');
    }
    return parts.join(' > ');
  };
  const kindOf = el => el.tagName === 'A' ? 'link' : 'button';
  const clickable = () => Array.from(document.querySelectorAll(
      'a, button, [role=button], [role=link], input[type=button]'));
  const isNextText = el => {
    for (const t of labelOf(el)) {
      const l = low(t);
      if (!l || l.length > 24) continue;
      if (PREVW.some(w => l.includes(w))) continue;
      if (NEXTX.includes(l)) return t;
      if (NEXTW.some(w => l.includes(w))) return t;
    }
    return null;
  };
  window.__gp = {visible, disabled, danger, cssPath, kindOf, clickable, isNextText, labelOf, norm, low,
    ok: el => visible(el) && !disabled(el) && !danger(el),
    // 現在のページ番号を探す
    currentNumber: () => {
      const marks = document.querySelectorAll('[aria-current]:not([aria-current=false]), .active, .current, .is-current, .selected, .is-active, [class*=current]');
      for (const m of marks) {
        const t = norm(m.innerText || m.textContent);
        if (/^\d{1,4}$/.test(t)) return parseInt(t, 10);
      }
      for (const a of document.querySelectorAll('a[href]')) {
        const t = norm(a.innerText || a.textContent);
        if (/^\d{1,4}$/.test(t) && a.href.split('#')[0] === location.href.split('#')[0]) return parseInt(t, 10);
      }
      // リンクでない数字（同じ親にリンクの数字がある）
      for (const e of document.querySelectorAll('span, strong, b, li, em')) {
        if (e.querySelector('a, button')) continue;
        const t = norm(e.innerText || e.textContent);
        if (!/^\d{1,4}$/.test(t)) continue;
        const p = e.parentElement;
        if (p && p.querySelector('a, button') && visible(e)) return parseInt(t, 10);
      }
      return null;
    },
  };
})();
""".replace("%DANGER%", repr(DANGER_WORDS).replace("'", '"')) \
   .replace("%NEXTW%", repr(NEXT_WORDS).replace("'", '"')) \
   .replace("%NEXTX%", repr(NEXT_EXACT).replace("'", '"')) \
   .replace("%PREVW%", repr(PREV_WORDS).replace("'", '"'))

_JS_FIND = r"""
(args) => {
  const g = window.__gp;
  const out = {};
  const info = (el, reason) => ({sel: g.cssPath(el), kind: g.kindOf(el), reason});
  // 1) 利用者が教えたセレクタ
  if (args.hint) {
    try {
      const el = document.querySelector(args.hint);
      if (el && g.ok(el)) out.hint = {sel: args.hint, kind: g.kindOf(el), reason: ''};
    } catch (e) {}
  }
  // 2) rel=next
  for (const el of document.querySelectorAll('a[rel~=next], link[rel~=next]')) {
    if (el.tagName === 'LINK') {
      if (el.href) { out.relLink = out.relLink || {sel: '', kind: 'link', reason: '', href: el.href}; }
      continue;
    }
    if (g.ok(el)) { out.rel = info(el, ''); break; }
  }
  // 3) 文字
  for (const el of g.clickable()) {
    if (!g.ok(el)) continue;
    const t = g.isNextText(el);
    if (t) { out.text = info(el, t); break; }
  }
  // 4) ページ番号
  const cur = g.currentNumber();
  out.cur = cur;
  if (cur !== null) {
    for (const el of g.clickable()) {
      if (g.norm(el.innerText || el.textContent) === String(cur + 1) && g.ok(el)) {
        out.number = info(el, String(cur + 1)); break;
      }
    }
  }
  return out;
}
"""

# 無限スクロールの試し中に、先に増えた分を follow が見落とさないための記録
_probe_base = {}


def _lib(page):
    page.evaluate(_JS_LIB)


def _safe_eval(page, js, arg=None, retries=3):
    """ページ遷移の最中などで失敗したら、読み込みを待ってやり直す"""
    last = None
    for _ in range(retries):
        try:
            return page.evaluate(js, arg) if arg is not None else page.evaluate(js)
        except Exception as e:  # 遷移中の実行コンテキスト破棄など
            last = e
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            time.sleep(0.2)
    raise last


def _height(page):
    return _safe_eval(page, "Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)")


def _signature(page):
    """ページの状態（URL・本文のハッシュ・高さ）"""
    js = """() => {
      const t = (document.body ? document.body.innerText : '') || '';
      let h = 0; for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) | 0;
      return [location.href, t.length, h,
        Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)];
    }"""
    return _safe_eval(page, js)


def _probe_scroll(page, tries=3, wait=0.8):
    """一番下まで送って、高さが伸びるかを2〜3回試す。伸びたら True"""
    base = _height(page)
    for _ in range(tries):
        before = _height(page)
        _safe_eval(page, "window.scrollTo(0, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight))")
        end = time.time() + wait
        while time.time() < end:
            time.sleep(0.1)
            if _height(page) > before:
                _probe_base[id(page)] = base
                return True
    return False


def find_next(page, hint_selector: str | None = None) -> "NextAction | None":
    """「次へ」に当たる操作を探す。無ければ None"""
    try:
        _lib(page)
        res = _safe_eval(page, _JS_FIND, {"hint": hint_selector or ""})
    except Exception:
        return None
    if res.get("hint"):
        h = res["hint"]
        return NextAction(h["kind"], h["sel"], "利用者が教えた「次へ」の場所（" + h["sel"] + "）")
    if res.get("rel"):
        r = res["rel"]
        return NextAction(r["kind"], r["sel"], "rel=\"next\" が付いたリンクを見つけた")
    if res.get("text"):
        t = res["text"]
        return NextAction(t["kind"], t["sel"], "「" + str(t["reason"]) + "」という文字の" + ("リンク" if t["kind"] == "link" else "ボタン") + "を見つけた")
    if res.get("number"):
        n = res["number"]
        return NextAction("number", n["sel"], "ページ番号 " + n["reason"] + " のリンク（現在は " + str(res.get("cur")) + " ページ目）")
    rl = res.get("relLink")
    if rl:  # <link rel="next"> だけがある場合は、そのURLへ移動
        return NextAction("link", "link[rel~=next]", "ページ上部の情報（link rel=\"next\"）に次のURLがある")
    if _probe_scroll(page):
        return NextAction("scroll", "", "一番下まで送ると続きが出る（無限スクロール）")
    return None


def _wait_change(page, before, wait_sec):
    """URL・本文・高さのどれかが変わるまで待つ。変われば True"""
    end = time.time() + max(wait_sec, 0.3)
    while time.time() < end:
        time.sleep(0.15)
        try:
            now = _signature(page)
        except Exception:
            continue
        if now[0] != before[0] or now[2] != before[2] or now[3] > before[3]:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            return True
    return False


def follow(page, action: "NextAction", wait_sec: float = 2.0) -> bool:
    """次へ進む。内容が変わったら True、変わらなければ False"""
    try:
        before = _signature(page)
        if action.kind == "scroll":
            base = _probe_base.pop(id(page), None)
            if base is not None and _height(page) > base:
                return True  # 試している間にもう増えていた
            for _ in range(2):
                _safe_eval(page, "window.scrollTo(0, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight))")
                if _wait_change(page, before, wait_sec):
                    return True
            return False

        _lib(page)
        # link[rel=next] だけの場合
        if action.selector == "link[rel~=next]":
            href = _safe_eval(page, "() => { const l = document.querySelector('link[rel~=next]'); return l ? l.href : ''; }")
            if not href or _looks_danger(href):
                return False
            page.goto(href, wait_until="domcontentloaded")
            return _wait_change(page, before, wait_sec) or _signature(page)[0] != before[0]

        el = page.query_selector(action.selector)
        if el is None:
            return False
        # 危ない文言・非表示・無効なら押さない
        if not page.evaluate("el => window.__gp.ok(el)", el):
            return False
        href = page.evaluate("el => el.tagName === 'A' ? el.href : ''", el)
        cur = before[0].split("#")[0]
        if href and not href.lower().startswith("javascript:") and href.split("#")[0] != cur:
            # 新しいタブを避けて、同じタブで href へ移動
            page.goto(href, wait_until="domcontentloaded")
            return _wait_change(page, before, wait_sec) or _signature(page)[0] != before[0]

        # JavaScript 型：クリック。新しいタブが開いたら閉じて同じタブで移動
        page.evaluate("el => { if (el.removeAttribute) el.removeAttribute('target'); }", el)
        popups = []

        def on_popup(pg):
            popups.append(pg)

        page.context.on("page", on_popup)
        try:
            el.scroll_into_view_if_needed(timeout=2000)
            el.click(timeout=3000)
            changed = _wait_change(page, before, wait_sec)
        finally:
            try:
                page.context.remove_listener("page", on_popup)
            except Exception:
                pass
        for p in popups:
            try:
                u = p.url
                p.close()
                if u and u != "about:blank" and not _looks_danger(u):
                    page.goto(u, wait_until="domcontentloaded")
                    return True
            except Exception:
                pass
        return changed
    except Exception:
        return False


def _looks_danger(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in DANGER_WORDS)


def _normalize(s: str) -> str:
    """空白・記号を取り除いて小文字にそろえる"""
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(c for c in s if unicodedata.category(c)[0] in ("L", "N")).lower()


def is_duplicate(prev_markdown: str, new_markdown: str) -> bool:
    """ほぼ同じ内容（95%以上一致）なら True"""
    a, b = _normalize(prev_markdown), _normalize(new_markdown)
    if a == b:
        return True
    if not a or not b:
        return False
    if abs(len(a) - len(b)) / max(len(a), len(b)) > 0.05:
        return False
    if len(a) > 30000:  # 長いときは先頭と末尾だけ比べる（速さのため）
        a, b = a[:15000] + a[-15000:], b[:15000] + b[-15000:]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.95 or sm.quick_ratio() < 0.95:
        return False
    return sm.ratio() >= 0.95


@dataclass
class NextAction:
    kind: str       # "link" | "button" | "number" | "scroll"
    selector: str   # クリックする要素（scroll のときは空）
    reason: str     # 見つけた根拠（日本語）


@dataclass
class StopRule:
    max_pages: int
    time_limit_sec: float


def should_stop(pages_done: int, started_at: float, rule: StopRule, esc_pressed: bool = False) -> str | None:
    """止める理由（日本語）。続けてよければ None。started_at は time.time() か time.monotonic() の値"""
    if esc_pressed:
        return "Esc キーが押されたので止めました。ここまでの分は保存できます。"
    if pages_done >= rule.max_pages:
        return f"最大ページ数（{rule.max_pages}ページ）に達したので止めました。続きが必要なら設定の「最大ページ数」を増やしてください。"
    now = time.time() if started_at > 1e9 else time.monotonic()
    if now - started_at >= rule.time_limit_sec:
        return f"制限時間（{int(rule.time_limit_sec)}秒）を過ぎたので止めました。続きが必要なら設定の「制限時間秒」を増やしてください。"
    return None
