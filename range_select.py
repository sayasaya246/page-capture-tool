# 画面PDF化アプリ（Web版）：担当⑤ 範囲選び
# ページ上で要素を選ぶ・サイトごとのルールを保存して適用する
# 注意：このファイル名は Python 標準の select と同名。スクリプト置き場が
# sys.path の先頭にあると標準の select を隠すので、呼ぶ側は importlib などで
# 別名（例：select_rules）で読み込むのが安全。
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class SiteRules:
    domain: str
    include_selector: str | None = None
    exclude_selectors: list[str] = field(default_factory=list)
    next_selector: str | None = None  # 「次へ」の要素（利用者が教えたもの）


# ---------- ドメイン ----------

def selector_for_domain(url: str) -> str:
    """URL からホスト名（ポートがあればポートつき）を返す。www. は除く"""
    u = (url or "").strip()
    if not u:
        raise ValueError("URL が空です。http:// か https:// から始まるURLを渡してください。")
    if "://" not in u:
        u = "//" + u  # スキーム無しでもホストを取り出せるように
    sp = urlsplit(u)
    host = (sp.hostname or "").lower()
    if not host:
        raise ValueError(f"URL からサイト名を取り出せませんでした：{url}\nhttp:// か https:// から始まるURLか確認してください。")
    if host.startswith("www."):
        host = host[4:]
    try:
        port = sp.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port else host


# ---------- 保存・読み込み ----------

def _read_all(path: str) -> list[dict]:
    """ファイル全体を読み、サイトの一覧を返す。無ければ空"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return []
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(
            f"サイト設定ファイルが壊れていて読めません：{path}\n"
            f"原因：{e}\n"
            "直し方：ファイルをメモ帳で開いて JSON の書き方（カンマ・かっこ・引用符）を直すか、"
            "ファイルを別の場所へ移して、もう一度ルールを保存してください。"
        ) from e
    sites = data.get("サイト") if isinstance(data, dict) else None
    if not isinstance(sites, list) or not all(isinstance(s, dict) and "domain" in s for s in sites):
        raise ValueError(
            f"サイト設定ファイルの形が想定と違います：{path}\n"
            '原因："サイト" の中に、domain を持つ項目の一覧が必要です。\n'
            "直し方：ファイルを別の場所へ移して、もう一度ルールを保存してください。"
        )
    return sites


def load_rules(path: str, domain: str) -> SiteRules | None:
    """domain のルールを返す。無ければ None"""
    key = domain.lower()
    for s in _read_all(path):
        if str(s["domain"]).lower() == key:
            return SiteRules(
                domain=s["domain"],
                include_selector=s.get("include_selector") or None,
                exclude_selectors=[x for x in (s.get("exclude_selectors") or [])
                                   if isinstance(x, str)] if isinstance(s.get("exclude_selectors"), list) else [],
                next_selector=s.get("next_selector") or None,
            )
    return None


def save_rules(path: str, rules: SiteRules) -> None:
    """サイトごとに1件で保存。同じドメインは更新"""
    sites = _read_all(path)
    item = {
        "domain": rules.domain,
        "include_selector": rules.include_selector,
        "exclude_selectors": list(rules.exclude_selectors),
        "next_selector": rules.next_selector,
    }
    key = rules.domain.lower()
    for i, s in enumerate(sites):
        if str(s["domain"]).lower() == key:
            sites[i] = item
            break
    else:
        sites.append(item)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    # 途中で止まっても壊れないよう、一時ファイルに書いてから入れ替える
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"サイト": sites}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------- ページ上の操作 ----------

_JS_INSTALL = r"""
(promptText) => {
  if (window.__pickState) return;
  const st = { done: false, selector: null, cancelled: false, el: null };
  window.__pickState = st;

  const box = document.createElement('div');
  box.setAttribute('data-pick-ui', '1');
  box.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;display:none;' +
    'border:2px solid #e8590c;background:rgba(232,89,12,0.18);box-sizing:border-box;';
  const label = document.createElement('div');
  label.setAttribute('data-pick-ui', '1');
  label.textContent = promptText + '（クリックで決定・Escで取り消し）';
  label.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;top:6px;left:50%;' +
    'transform:translateX(-50%);background:#212529;color:#fff;font:13px/1.4 sans-serif;' +
    'padding:4px 10px;border-radius:4px;max-width:90vw;';
  document.documentElement.appendChild(box);
  document.documentElement.appendChild(label);

  const isUi = (n) => n && n.getAttribute && n.getAttribute('data-pick-ui');
  let hover = null;
  const place = () => {
    if (!hover || !hover.isConnected) { box.style.display = 'none'; return; }
    const r = hover.getBoundingClientRect();
    box.style.display = 'block';
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  };

  // 安定した class か（数字だらけ・自動生成っぽい名前は避ける）
  const goodClass = (c) => {
    if (!c || c.length > 30) return false;
    const digits = (c.match(/\d/g) || []).length;
    if (digits >= 3 || digits / c.length > 0.3) return false;
    if (/^(css|sc|jsx|styled|emotion|_)[-_]?[A-Za-z0-9]{4,}$/.test(c) && /\d|[A-Z]/.test(c)) return false;
    if (/^[a-z]{0,3}[A-Z0-9_-]{6,}$/.test(c) && /\d/.test(c)) return false;
    if (/^(active|hover|focus|selected|open|show|hidden|visible|current)$/i.test(c)) return false;
    return true;
  };
  const goodId = (id) => id && !/\d{4,}/.test(id) && id.length <= 40;
  const count = (sel) => { try { return document.querySelectorAll(sel); } catch (e) { return []; } };
  const unique = (sel, el) => { const l = count(sel); return l.length === 1 && l[0] === el; };
  const esc = (s) => CSS.escape(s);

  const build = (el) => {
    const tag = el.tagName.toLowerCase();
    if (goodId(el.id) && unique('#' + esc(el.id), el)) return '#' + esc(el.id);
    const cls = Array.from(el.classList).filter(goodClass);
    for (const c of cls) {
      if (unique(tag + '.' + esc(c), el)) return tag + '.' + esc(c);
      if (unique('.' + esc(c), el)) return '.' + esc(c);
    }
    if (cls.length > 1) {
      const all = tag + cls.map(c => '.' + esc(c)).join('');
      if (unique(all, el)) return all;
    }
    // 親をたどる形：タグ＋nth-of-type
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      if (cur !== el && goodId(cur.id) && unique('#' + esc(cur.id), cur)) {
        parts.unshift('#' + esc(cur.id)); break;
      }
      const t = cur.tagName.toLowerCase();
      if (t === 'body') { parts.unshift('body'); break; }
      let seg = t;
      const p = cur.parentElement;
      if (p) {
        const same = Array.from(p.children).filter(x => x.tagName === cur.tagName);
        if (same.length > 1) seg += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(seg);
      const sel = parts.join(' > ');
      if (unique(sel, el)) return sel;
      cur = p;
    }
    return parts.join(' > ');
  };

  const stop = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
  const handlers = {
    mouseover: (e) => { if (!isUi(e.target)) { hover = e.target; place(); } },
    mousemove: (e) => { if (!isUi(e.target) && hover !== e.target) { hover = e.target; place(); } },
    scroll: () => place(),
    resize: () => place(),
    click: (e) => {
      stop(e);
      if (st.done) return;
      const el = e.target;
      if (!el || el.nodeType !== 1 || isUi(el)) return;
      st.el = el;
      st.selector = build(el);
      st.done = true;
    },
    keydown: (e) => {
      if (e.key === 'Escape' || e.key === 'Esc') { stop(e); st.cancelled = true; st.done = true; }
    },
  };
  // クリックに関わる他のイベントも止める（リンク移動・ボタン動作を防ぐ）
  for (const n of ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'auxclick', 'dblclick', 'contextmenu', 'submit']) {
    handlers[n] = stop;
  }
  for (const n in handlers) window.addEventListener(n, handlers[n], true);

  st.cleanup = () => {
    for (const n in handlers) window.removeEventListener(n, handlers[n], true);
    box.remove(); label.remove();
  };
}
"""

_JS_CHECK = "(s) => { const st = window.__pickState; return !!st && !!st.el && document.querySelector(s) === st.el; }"

_JS_CLEAN = """() => {
  const st = window.__pickState;
  if (st && st.cleanup) st.cleanup();
  document.querySelectorAll('[data-pick-ui]').forEach(n => n.remove());
  delete window.__pickState;
}"""


def pick_element(page, prompt: str, timeout_sec: float = 120) -> str | None:
    """マウスを乗せた要素を枠で光らせ、クリックされた要素の CSS セレクタを返す。
    Esc・時間切れ・確認に失敗したときは None"""
    deadline = time.monotonic() + timeout_sec
    result = None
    try:
        page.evaluate(_JS_INSTALL, prompt)
        while time.monotonic() < deadline:
            try:
                st = page.evaluate(
                    "() => window.__pickState ? {done: window.__pickState.done, "
                    "cancelled: window.__pickState.cancelled, selector: window.__pickState.selector} : null"
                )
            except Exception:
                # ページが移動して仕込みが消えた：入れ直す
                page.wait_for_timeout(200)
                try:
                    page.evaluate(_JS_INSTALL, prompt)
                except Exception:
                    pass
                continue
            if st is None:
                try:
                    page.evaluate(_JS_INSTALL, prompt)
                except Exception:
                    pass
            elif st["done"]:
                sel = st["selector"]
                if not st["cancelled"] and sel:
                    # 返す前に、同じ要素を指しているか確かめる
                    ok = page.evaluate(_JS_CHECK, sel)
                    if ok and page.query_selector(sel) is not None:
                        result = sel
                break
            page.wait_for_timeout(80)
    finally:
        try:
            page.evaluate(_JS_CLEAN)
        except Exception:
            pass  # ページが閉じている等。後始末できないだけなので無視
    return result


_JS_APPLY = """(sels) => {
  for (const s of sels) {
    try { document.querySelectorAll(s).forEach(n => n.remove()); } catch (e) { /* 不正なセレクタは無視 */ }
  }
}"""


def apply_rules(page, rules: SiteRules) -> None:
    """exclude_selectors の要素を DOM から取り除く。不正・見つからないものは無視"""
    sels = [s for s in (rules.exclude_selectors or []) if isinstance(s, str) and s.strip()]
    if sels:
        page.evaluate(_JS_APPLY, sels)
