# 画面PDF化アプリ（Web版）：全体テスト
# 見本サーバーを自分で起動・停止し、見本ページと各部品（web / output / paginate / select）を確かめて合否を日本語で一覧表示する。
# 実行：%LOCALAPPDATA%\Programs\Python\Python312\python.exe -X utf8 -W ignore run_tests.py
# 部品のファイルや関数がまだ無いときは「未実装（スキップ）」にして先へ進む（失敗にしない）。
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(HERE)                    # スクリプト\
BASE = os.path.join(tempfile.gettempdir(), "画面PDF化アプリ_テスト", "全体")   # テスト用の保存先（終わったら消す）
_G = r"G:\マイドライブ\画面PDF化アプリ\見本"
_C = os.path.join(os.path.dirname(SCRIPT_DIR), "見本")  # 画面PDF化アプリ\見本（G:\マイドライブ が使えないときの Drive の写し）
SAMPLE_DIR = os.environ.get("GAMEN_PDF_SAMPLES", _G if os.path.isdir(_G) else _C)
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"

# 設定は一時ファイルに向ける（Drive の設定ファイルを作らない・書き換えない）
os.makedirs(BASE, exist_ok=True)
_cfg = os.path.join(BASE, "config.json")
with open(_cfg, "w", encoding="utf-8") as f:
    json.dump({"作成者名": "テスト", "保存先": os.path.join(BASE, "成果物"),
               "サイト設定ファイル": os.path.join(BASE, "サイト設定.json"),
               "専用Chromeの場所": os.path.join(BASE, "chrome_profile_cfg"),
               "ページ待ち秒": 0.3}, f, ensure_ascii=False)
os.environ["GAMEN_PDF_CONFIG"] = _cfg
# スクリプト\ には select.py があり標準の select と同名のため、先頭ではなく末尾に足す（標準の select を隠さない）
import select, selectors, socketserver, http.server  # noqa: E401  先に標準版を読み込んでおく
sys.path.append(SCRIPT_DIR)

RESULTS = []   # (区分, 名前, 状態, 詳細)


class Skip(Exception):
    """未実装（スキップ）"""


def record(group, name, status, detail=""):
    RESULTS.append((group, name, status, detail))
    mark = {"合格": "[合格]", "不合格": "[不合格]", "スキップ": "[未実装（スキップ）]"}[status]
    print(f"{mark} {group} / {name}" + (f"  … {detail}" if detail else ""), flush=True)


def check(group, name, fn, *args):
    try:
        d = fn(*args)
        record(group, name, "合格", d or "")
    except Skip as e:
        record(group, name, "スキップ", str(e))
    except AssertionError as e:
        record(group, name, "不合格", str(e))
    except Exception as e:   # 想定外の例外
        tb = traceback.extract_tb(e.__traceback__)[-1]
        record(group, name, "不合格", f"{type(e).__name__}: {str(e)[:200]}（{os.path.basename(tb.filename)}:{tb.lineno}）")


def load_part(fname, alias):
    """部品を読み込む。ファイルが無ければ None。select.py は標準の select と名前がぶつかるので別名で読む"""
    p = os.path.join(SCRIPT_DIR, fname)
    if not os.path.exists(p):
        return None
    spec = importlib.util.spec_from_file_location(alias, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m
    spec.loader.exec_module(m)
    return m


def need(mod, *names):
    """モジュールと関数がそろっているか。無ければ Skip"""
    if mod is None:
        raise Skip("ファイルがまだありません")
    for n in names:
        if not hasattr(mod, n):
            raise Skip(f"{n} がまだありません")


# ---------- 見本の期待値（見本の説明.md から読む） ----------
def load_expect():
    exp = {}
    p = os.path.join(SAMPLE_DIR, "見本の説明.md")
    text = open(p, encoding="utf-8").read()
    for m in re.finditer(r"### (\S+\.html)\n- 何のテストか：.*\n- 期待される本文の最初の一文：(.*)\n- 期待される本文の最後の一文：(.*)\n", text):
        exp[m.group(1)] = (m.group(2).strip(), m.group(3).strip())
    return exp


EXPECT = {}


# =================== A. 見本ページ自体の確認（部品に頼らない） ===================
def goto(page, name):
    r = page.goto(URL + name, wait_until="load")
    assert r is not None and r.status == 200, f"{name} が開けません（状態 {r.status if r else '不明'}）。サーバーと見本フォルダを確認してください"
    page.wait_for_timeout(300)


def art_text(page):
    return page.inner_text("#main-article")


def a_all_open(page, external):
    names = sorted(n for n in os.listdir(SAMPLE_DIR) if n.endswith(".html"))
    assert len(names) >= 13, f"見本HTMLが {len(names)} 件しかありません（13件以上のはず）"
    for n in names:
        goto(page, n)
        for sel in ("#main-article", ".ad", "aside .side-card", "#fixed-menu", "#site-footer"):
            assert page.query_selector(sel), f"{n} に {sel} がありません"
        pos = page.eval_on_selector("#fixed-menu", "e=>getComputedStyle(e).position")
        assert pos == "fixed", f"{n} の固定メニューが fixed ではありません"
    assert not external, f"外部への通信がありました：{external[:3]}"
    return f"{len(names)}ページすべて開けた・外部通信なし"


def a_static(page, name):
    f, l = EXPECT[name]
    goto(page, name)
    t = art_text(page)
    assert f in t, f"{name} の本文に最初の一文がありません"
    assert l in t, f"{name} の本文に最後の一文がありません"
    assert "広告：" not in t and "人気ランキング" not in t, f"{name} の本文エリアに広告・サイドが混ざっています"
    return "先頭・末尾の文あり、本文に広告なし"


def a_next_link(page):
    for i in (1, 2, 3):
        n = f"next_link_{i}.html"
        goto(page, n)
        f, l = EXPECT[n]
        t = art_text(page)
        assert f in t and l in t, f"{n} の本文が期待と違います"
        nxt = page.query_selector("a[rel=next]")
        if i < 3:
            assert nxt and "次のページ" in nxt.inner_text(), f"{n} に「次のページ」リンクがありません"
            nxt.click()
            page.wait_for_load_state("load")
            assert page.url.endswith(f"next_link_{i+1}.html"), f"{n} から次へ進めません"
        else:
            assert nxt is None, "最終ページに次へリンクが残っています"
    return "1→2→3 とたどれ、3で終わる"


def a_number(page):
    goto(page, "number_1.html")
    for i in (1, 2, 3):
        n = f"number_{i}.html"
        assert page.url.endswith(n), f"{n} に来ていません"
        f, l = EXPECT[n]
        t = art_text(page)
        assert f in t and l in t, f"{n} の本文が期待と違います"
        assert page.inner_text(".pager .current").strip() == str(i), "今の番号の強調が違います"
        if i < 3:
            page.click(f".pager a:text-is('{i+1}')")
            page.wait_for_load_state("load")
    assert page.query_selector(".pager a:text-is('4')") is None
    return "番号リンク 1→2→3 とたどれる"


def a_button(page):
    goto(page, "button.html")
    f, l = EXPECT["button.html"]
    assert f in art_text(page), "1ページ目の最初の文がありません"
    seen = [page.inner_text("#page-title")]
    for _ in range(2):
        page.click("#next-btn")
        page.wait_for_timeout(200)
        seen.append(page.inner_text("#page-title"))
    assert len(set(seen)) == 3, f"3ページ分に切り替わりませんでした：{seen}"
    assert l in art_text(page), "3ページ目の最後の文がありません"
    assert not page.is_visible("#next-btn"), "最後のページでボタンが残っています"
    assert page.url.endswith("button.html"), "URLが変わっています（変わらないはず）"
    return "ボタンで3ページ切替、URLは不変、最後でボタンが消える"


def scroll_bottom(page):
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(500)


def a_scroll(page):
    goto(page, "scroll.html")
    f, _ = EXPECT["scroll.html"]
    assert f in art_text(page)
    n0 = page.eval_on_selector_all(".chunk", "e=>e.length")
    assert page.query_selector("#end-mark") is None, "最初から「これで終わり」が出ています"
    for _ in range(15):
        if page.query_selector("#end-mark"):
            break
        scroll_bottom(page)
    assert page.query_selector("#end-mark"), "下までスクロールしても「これで終わり」が出ません"
    n1 = page.eval_on_selector_all(".chunk", "e=>e.length")
    assert n1 == 4 and n0 == 1, f"ブロック数が期待と違います（最初{n0}→最後{n1}、期待 1→4）"
    assert art_text(page).strip().endswith("これで終わり")
    return "スクロールで1→4ブロックに増え、最後に「これで終わり」"


def a_cookie(page, ctx):
    ctx.clear_cookies()
    goto(page, "cookie.html")
    assert page.is_visible("#cookie-banner"), "Cookieバナーが出ていません"
    page.click("#accept-cookies")
    page.wait_for_timeout(200)
    assert page.query_selector("#cookie-banner") is None, "同意してもバナーが消えません"
    page.reload()
    assert page.query_selector("#cookie-banner") is None, "再読込でバナーがまた出ました（Cookie が保存されていない）"
    f, l = EXPECT["cookie.html"]
    t = art_text(page)
    assert f in t and l in t
    ctx.clear_cookies()
    return "バナー表示→同意で消える→再読込でも出ない"


def a_lazy(page):
    goto(page, "lazy.html")
    n_total = page.eval_on_selector_all(".lazy", "e=>e.length")
    loaded0 = page.eval_on_selector_all(".lazy-body[data-loaded]", "e=>e.length")
    assert loaded0 < n_total, f"スクロール前にすべて読み込まれています（{loaded0}/{n_total}）"
    f, l = EXPECT["lazy.html"]
    assert l not in art_text(page), "スクロール前に最後の章が出ています"
    for _ in range(40):
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(120)
    page.wait_for_timeout(500)
    t = art_text(page)
    assert f in t and l in t and "（読み込み中）" not in t, "スクロール後も本文が揃いません"
    return f"スクロール前 {loaded0}/{n_total} → 後は全章そろう"


def a_protected(page):
    goto(page, "protected.html")
    us = page.eval_on_selector("#main-article", "e=>getComputedStyle(e).userSelect")
    assert us == "none", f"user-select が none ではありません（{us}）"
    for a in ("oncopy", "oncontextmenu", "onselectstart"):
        assert page.eval_on_selector("body", f"e=>e.getAttribute('{a}')"), f"body に {a} がありません"
    return "user-select:none と oncopy 等の禁止が再現できた"


def a_mixed(page):
    goto(page, "mixed.html")
    f, l = EXPECT["mixed.html"]
    t = art_text(page)
    assert f in t and l in t
    for sel in ("table th", "table td", "h2", "h3", "ul li", "ol li"):
        assert page.query_selector("#main-article " + sel), f"{sel} がありません"
    assert re.search(r"[A-Za-z]{5,}", t), "英語がありません"
    return "日本語・英語・表・見出し・リストあり"


# =================== B. 部品のテスト ===================
def mk_rules(sel_mod, **kw):
    if sel_mod is not None and hasattr(sel_mod, "SiteRules"):
        return sel_mod.SiteRules(**kw)
    return SimpleNamespace(include_selector=kw.get("include_selector"),
                           exclude_selectors=kw.get("exclude_selectors", []), next_selector=kw.get("next_selector"),
                           domain=kw.get("domain", ""))


def web_tests(web, sel_mod):
    G = "web"
    from common import ProtectedPage
    try:
        need(web, "BrowserSession")
    except Skip as e:
        for n in ("本文を取り出す（広告除外）", "ルール指定の取り出し", "Cookieバナーを閉じる", "遅延読み込みの本文", "コピー禁止で止まる", "表・英語の取り出し", "detect_protection"):
            record(G, n, "スキップ", str(e))
        return
    prof = os.path.join(BASE, "chrome_profile_web")
    try:
        bs = web.BrowserSession(prof, headless=True)
        sess = bs.__enter__()
    except Exception as e:
        record(G, "BrowserSession を開く", "不合格", f"{type(e).__name__}: {str(e)[:200]}")
        return
    try:
        def load(name, prepare=True):
            sess.goto(URL + name)
            if prepare:
                sess.prepare()

        def t_article():
            load("article.html")
            md = sess.extract().markdown
            f, l = EXPECT["article.html"]
            assert f in md and l in md, "本文の最初か最後の文が取れていません"
            for bad in ("広告：", "人気ランキング", "筆者について", "プライバシー・利用規約"):
                assert bad not in md, f"広告・サイド・フッターの文字「{bad}」が混ざっています"
            return "本文のみ取り出せた"
        check(G, "本文を取り出す（広告除外）", t_article)

        def t_rules():
            load("article.html")
            r = mk_rules(sel_mod, domain="127.0.0.1:8765", include_selector="#main-article",
                         exclude_selectors=[".ad", "aside .side-card", "#fixed-menu", "#site-footer"])
            md = sess.extract(rules=r).markdown
            f, l = EXPECT["article.html"]
            assert f in md and l in md and "広告：" not in md and "（本文を自動判定できませんでした）" not in md
            return "include/exclude 指定で取り出せた"
        check(G, "ルール指定の取り出し", t_rules)

        def t_cookie():
            load("cookie.html")
            el = sess.page.query_selector("#cookie-banner")
            assert el is None or not el.is_visible(), "prepare 後も Cookie バナーが残っています"
            md = sess.extract().markdown
            assert EXPECT["cookie.html"][1] in md and "Cookie（クッキー）を使います" not in md
            return "バナーを閉じて本文だけ取れた"
        check(G, "Cookieバナーを閉じる", t_cookie)

        def t_lazy():
            load("lazy.html")
            md = sess.extract().markdown
            f, l = EXPECT["lazy.html"]
            assert f in md and l in md, "遅延読み込みの本文（最初／最後の章）が取れていません"
            assert "（読み込み中）" not in md
            return "全章そろった"
        check(G, "遅延読み込みの本文", t_lazy)

        def t_protected():
            sess.goto(URL + "protected.html")
            try:
                sess.prepare()
            except ProtectedPage as e:
                assert str(e).strip(), "ProtectedPage のメッセージが空です"
                return f"ProtectedPage で停止：{str(e)[:50]}"
            raise AssertionError("コピー禁止ページで ProtectedPage が出ませんでした（突破せず止めるのが約束）")
        check(G, "コピー禁止で止まる", t_protected)

        def t_mixed():
            load("mixed.html")
            md = sess.extract().markdown
            for s in ("道具の一覧表", "Glass tank", "LED, timer needed", "Add water slowly", EXPECT["mixed.html"][1]):
                assert s in md, f"「{s}」が取れていません"
            return "表・英語・見出し・リストの文字が残った"
        check(G, "表・英語の取り出し", t_mixed)

        def t_detect():
            need(web, "detect_protection")
            sess.goto(URL + "article.html")
            assert web.detect_protection(sess.page) is None, "普通の記事を保護ページと判定しています"
            sess.goto(URL + "protected.html")
            r = web.detect_protection(sess.page)
            assert isinstance(r, str) and r, "コピー禁止ページを検出できません"
            return f"検出：{r[:50]}"
        check(G, "detect_protection", t_detect)
    finally:
        try:
            bs.__exit__(None, None, None)
        except Exception:
            pass


def output_tests(out):
    G = "output"
    import fitz
    from common import Document, PageData
    names = ("書き出し（md・pdf）", "通し番号をそろえる", "Markdown分割", "画面の写真ページ")
    try:
        need(out, "write_outputs")
    except Skip as e:
        for n in names:
            record(G, n, "スキップ", str(e))
        return
    odir = os.path.join(BASE, "出力")
    shutil.rmtree(odir, ignore_errors=True)
    now = datetime.now()
    s1 = "水草水槽は、魚を飼う水槽よりも先に、水と光と底砂の準備を整えるところからはじまります。"
    s2 = "Thank you for reading this English sentence."
    md1 = f"# 見出しテスト\n\n{s1}\n\n| 道具 | 価格 |\n|---|---|\n| 水槽 | 8,000円 |\n\n- 項目A\n- 項目B\n"
    md2 = f"## 二枚目\n\n{s2}\n\n" + "長い段落です。" * 80
    doc = Document("出力テスト用の題名", "http://127.0.0.1:8765/a.html", now,
                   [PageData("http://127.0.0.1:8765/a.html", "p1", now, md1),
                    PageData("http://127.0.0.1:8765/b.html", "p2", now, md2)])

    def t_basic():
        paths = out.write_outputs(doc, odir, "テスト作者")
        mds = [p for p in paths if p.endswith(".md")]
        pdfs = [p for p in paths if p.endswith(".pdf")]
        assert mds and pdfs, f"md か pdf が作られていません：{paths}"
        for p in paths:
            assert os.path.exists(p) and os.path.getsize(p) > 0, f"空のファイル：{p}"
            assert re.match(r"^[^\\/:*?\"<>|\s.]+(（\d+）)?(_分割\d+)?\.(md|pdf)$", os.path.basename(p)), f"ファイル名の形が約束と違います：{os.path.basename(p)}"
        txt = open(mds[0], encoding="utf-8").read()
        for s in ("出力テスト用の題名", "http://127.0.0.1:8765/a.html", "テスト作者", "ページ数", "## ページ2（http://127.0.0.1:8765/b.html）", s1, s2):
            assert s in txt, f"Markdown に「{s}」がありません"
        d = fitz.open(pdfs[0])
        allt = "".join(pg.get_text() for pg in d)
        flat = re.sub(r"\s+", "", allt)
        for s in (s1, s2):
            assert re.sub(r"\s+", "", s) in flat, f"PDFから文字を取り出せません（文字化け／検索不可）：{s[:15]}…"
        for i, pg in enumerate(d):
            t = pg.get_text()
            assert "テスト作者" in t and "http://127.0.0.1:8765/a.html" in t, f"PDF {i+1}ページ目の下に作成者・出典URLがありません"
        return f"md {len(mds)}・pdf {len(pdfs)}（{len(d)}ページ）、文字抽出一致、フッターあり"
    check(G, names[0], t_basic)

    def t_num():
        before = set(os.listdir(odir))
        paths = out.write_outputs(doc, odir, "テスト作者")
        stems = {os.path.splitext(os.path.basename(p))[0] for p in paths}
        assert len(stems) == 1, f"md と pdf の名前がそろっていません：{stems}"
        assert set(before) <= set(os.listdir(odir)), "既存のファイルが消えています"
        assert all(os.path.basename(p) not in before for p in paths), "前回と同じ名前で上書きしています"
        return f"名前 {stems.pop()}"
    check(G, names[1], t_num)

    def t_split():
        d2 = os.path.join(BASE, "出力分割")
        paths = out.write_outputs(doc, d2, "テスト作者", split_chars=300)
        mds = sorted(p for p in paths if p.endswith(".md"))
        assert len(mds) >= 2 and any("_分割1" in p for p in mds) and any("_分割2" in p for p in mds), f"分割されていません：{[os.path.basename(p) for p in mds]}"
        return f"{len(mds)}個に分割"
    check(G, names[2], t_split)

    def t_shot():
        from PIL import Image
        import io
        im = Image.new("RGB", (400, 300), (200, 220, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        now2 = datetime.now()
        pd = PageData("http://127.0.0.1:8765/a.html", "p", now2, md1, screenshot_png=buf.getvalue())
        dd = Document("写真テスト", pd.url, now2, [pd])
        pa = [p for p in out.write_outputs(dd, os.path.join(BASE, "出力写真a"), "作者", with_screenshot=False) if p.endswith(".pdf")][0]
        pb = [p for p in out.write_outputs(dd, os.path.join(BASE, "出力写真b"), "作者", with_screenshot=True) if p.endswith(".pdf")][0]
        na, nb = len(fitz.open(pa)), len(fitz.open(pb))
        assert nb > na, f"写真ありでもページが増えていません（なし{na}→あり{nb}）"
        return f"ページ数 {na}→{nb}"
    check(G, names[3], t_shot)


def paginate_tests(pg, page):
    G = "paginate"
    names = ("リンク型", "番号型", "ボタン型", "無限スクロール型", "次が無いページ", "hint_selector", "is_duplicate", "should_stop")
    try:
        need(pg, "find_next", "follow")
    except Skip as e:
        for n in names[:6]:
            record(G, n, "スキップ", str(e))
        need_ok = False
    else:
        need_ok = True

    def t_link():
        goto(page, "next_link_1.html")
        for i in (1, 2):
            a = pg.find_next(page)
            assert a is not None and a.kind == "link", f"next_link_{i} で link を見つけられません（{a}）"
            assert a.reason, "reason（根拠）が空です"
            assert pg.follow(page, a) is True and page.url.endswith(f"next_link_{i+1}.html"), "follow で次のページへ進めません"
        assert pg.find_next(page) is None, "最終ページで None になりません"
        return "link 1→2→3、最後は None"

    def t_number():
        goto(page, "number_1.html")
        for i in (1, 2):
            a = pg.find_next(page)
            assert a is not None and a.kind == "number", f"number_{i} で number を見つけられません（{a}）"
            assert pg.follow(page, a) is True and page.url.endswith(f"number_{i+1}.html"), "番号リンクで進めません"
        assert pg.find_next(page) is None, "最終ページで None になりません"
        return "number 1→2→3、最後は None"

    def t_button():
        goto(page, "button.html")
        for _ in range(2):
            before = page.inner_text("#page-title")
            a = pg.find_next(page)
            assert a is not None and a.kind == "button", f"button を見つけられません（{a}）"
            assert pg.follow(page, a) is True and page.inner_text("#page-title") != before, "ボタンで内容が変わりません"
        assert pg.find_next(page) is None, "最後のページで None になりません"
        return "button 1→2→3、最後は None"

    def t_scroll():
        goto(page, "scroll.html")
        n = 0
        for _ in range(10):
            a = pg.find_next(page)
            if a is None:
                break
            assert a.kind == "scroll", f"scroll を期待しましたが {a.kind} でした"
            if not pg.follow(page, a):
                break
            n += 1
        assert n >= 3, f"スクロールで続きを読み込めた回数が少なすぎます（{n}回、期待3回以上）"
        assert page.query_selector("#end-mark"), "最後（これで終わり）まで届いていません"
        assert pg.find_next(page) is None, "終わりのあとで None になりません"
        return f"scroll {n}回で最後まで、その後 None"

    def t_none():
        for n in ("article.html", "mixed.html"):
            goto(page, n)
            a = pg.find_next(page)
            assert a is None, f"{n} は次が無いのに {a.kind} を返しました"
        return "次が無いページで None"

    def t_hint():
        goto(page, "next_link_1.html")
        a = pg.find_next(page, ".pager a")
        assert a is not None and a.selector, "hint_selector で見つかりません"
        assert pg.follow(page, a) is True and page.url.endswith("next_link_2.html")
        goto(page, "next_link_1.html")
        a2 = pg.find_next(page, "#存在しない要素")
        assert a2 is not None, "hint が無効でも通常の探し方に進むはずです"
        return "hint で進めた・無効な hint は通常探索へ"

    if need_ok:
        for n, f in zip(names[:6], (t_link, t_number, t_button, t_scroll, t_none, t_hint)):
            check(G, n, f)

    def t_dup():
        need(pg, "is_duplicate")
        a = "水草水槽は、魚を飼う水槽よりも先に準備を整える。" * 20
        assert pg.is_duplicate(a, a) is True, "同じ内容で False"
        assert pg.is_duplicate(a, a + "あ") is True, "ほぼ同じ内容で False"
        b = "庭づくりの出発点は、土の状態を知ることです。" * 20
        assert pg.is_duplicate(a, b) is False, "違う内容で True"
        return "同一・ほぼ同一は True、別内容は False"
    check(G, names[6], t_dup)

    def t_stop():
        need(pg, "should_stop", "StopRule")
        ok = []
        for conv in (time.time, time.monotonic):    # started_at の時計がどちらでも通るようにする
            r = pg.StopRule(max_pages=50, time_limit_sec=600)
            try:
                if (pg.should_stop(1, conv() - 1000, r) and pg.should_stop(1, conv(), r) is None):
                    ok.append(conv.__name__)
            except Exception:
                pass
        assert ok, "時間切れの判定が合いません（started_at は time.time() か time.monotonic() の値）"
        r = pg.StopRule(max_pages=50, time_limit_sec=600)
        conv = getattr(time, ok[0])
        assert pg.should_stop(50, conv(), r), "最大ページ数で止まりません"
        assert pg.should_stop(1, conv(), r, esc_pressed=True), "Esc で止まりません"
        assert pg.should_stop(3, conv(), r) is None, "止める理由が無いのに止まります"
        return f"最大ページ・時間・Esc で停止（started_at の時計：{ok[0]}）"
    check(G, names[7], t_stop)


def select_tests(sm, page):
    G = "select"
    names = ("保存と読み込み", "ドメイン更新・別ドメイン", "selector_for_domain", "apply_rules（取り除く）", "pick_element（クリック）", "pick_element（Esc）", "pick_element（時間切れ）")
    try:
        need(sm, "SiteRules", "load_rules", "save_rules")
    except Skip as e:
        for n in names[:2]:
            record(G, n, "スキップ", str(e))
    else:
        path = os.path.join(BASE, "サイト設定テスト.json")

        def t_save():
            assert sm.load_rules(path, "127.0.0.1:8765") is None, "ファイルが無いときは None のはずです"
            r = sm.SiteRules(domain="127.0.0.1:8765", include_selector="#main-article",
                             exclude_selectors=[".ad", "#広告"], next_selector="#next-btn")
            sm.save_rules(path, r)
            json.loads(open(path, encoding="utf-8").read())
            g = sm.load_rules(path, "127.0.0.1:8765")
            assert g is not None and g.include_selector == "#main-article" and g.exclude_selectors == [".ad", "#広告"] and g.next_selector == "#next-btn", f"読み戻した内容が違います：{g}"
            return "UTF-8のJSONで保存、同じ内容を読み戻せた"
        check(G, names[0], t_save)

        def t_update():
            sm.save_rules(path, sm.SiteRules(domain="example.test"))
            sm.save_rules(path, sm.SiteRules(domain="127.0.0.1:8765", include_selector="#new"))
            g = sm.load_rules(path, "127.0.0.1:8765")
            assert g.include_selector == "#new" and not g.exclude_selectors, "同じドメインが更新されていません"
            assert sm.load_rules(path, "example.test") is not None, "別ドメインの設定が消えました"
            d = json.loads(open(path, encoding="utf-8").read())
            s = json.dumps(d, ensure_ascii=False)
            assert s.count("127.0.0.1:8765") == 1, "同じドメインが複数件になっています（1サイト1件のはず）"
            return "同じドメインは更新、別ドメインは残る"
        check(G, names[1], t_update)

    def t_dom():
        need(sm, "selector_for_domain")
        r = sm.selector_for_domain("http://127.0.0.1:8765/article.html")
        assert r in ("127.0.0.1:8765", "127.0.0.1"), f"ドメインが取れていません：{r}"
        return r
    check(G, names[2], t_dom)

    def t_apply():
        need(sm, "SiteRules", "apply_rules")
        goto(page, "article.html")
        r = sm.SiteRules(domain="127.0.0.1:8765", exclude_selectors=[".ad", "aside .side-card", "#fixed-menu", "#site-footer"])
        sm.apply_rules(page, r)
        for s in r.exclude_selectors:
            assert page.query_selector(s) is None, f"{s} がページに残っています（display:none ではなく remove のはず）"
        assert page.query_selector("#main-article"), "本文まで消えています"
        return "広告・サイド・メニュー・フッターを取り除いた"
    check(G, names[3], t_apply)

    fire = """([sel, mode]) => {
      const t = document.querySelector(sel); window.__t = t;
      setTimeout(() => {
        if (mode === 'esc') { document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', keyCode:27, bubbles:true})); return; }
        t.scrollIntoView({block:'center'});
        const r = t.getBoundingClientRect();
        const o = {bubbles:true, cancelable:true, clientX:r.left+r.width/2, clientY:r.top+r.height/2, view:window};
        ['mouseover','mousemove'].forEach(n => t.dispatchEvent(new MouseEvent(n, o)));
        setTimeout(() => t.dispatchEvent(new MouseEvent('click', o)), 400);
      }, 1200);
    }"""

    def t_pick():
        need(sm, "pick_element")
        goto(page, "number_1.html")
        sel = 'a[href="number_2.html"]'
        page.evaluate(fire, [sel, "click"])
        got = sm.pick_element(page, "テスト：番号2を選んでください", 15)
        assert got, "クリックしたのにセレクタが返りません（None）"
        ok = page.evaluate("s => document.querySelectorAll(s).length === 1 && document.querySelector(s) === window.__t", got)
        assert ok, f"返ったセレクタ「{got}」が、クリックした要素を1つだけ指していません"
        assert page.url.endswith("number_1.html"), "選んだだけなのにリンク先へ移動しました（元のクリック動作を止めるはず）"
        return f"返ったセレクタ：{got}（ページ移動なし）"
    check(G, names[4], t_pick)

    def t_esc():
        need(sm, "pick_element")
        goto(page, "article.html")
        page.evaluate(fire, ["#main-article h1", "esc"])
        t0 = time.time()
        got = sm.pick_element(page, "テスト：Esc で中止", 15)
        assert got is None, f"Esc で None になりません（{got}）"
        assert time.time() - t0 < 8, "Esc を押しても止まるのが遅すぎます"
        return "Esc で None"
    check(G, names[5], t_esc)

    def t_timeout():
        need(sm, "pick_element")
        goto(page, "article.html")
        t0 = time.time()
        got = sm.pick_element(page, "テスト：何もしない", 1.5)
        assert got is None, f"時間切れで None になりません（{got}）"
        assert time.time() - t0 < 6, "時間切れの判定が遅すぎます"
        return "時間切れで None"
    check(G, names[6], t_timeout)


# =================== 本体 ===================
def main():
    global EXPECT
    srv = None
    try:
        # 見本サーバーを起動
        try:
            mod = load_part(os.path.join("テスト", "見本サーバー.py"), "mihon_server")
            srv, _ = mod.start_background(PORT, SAMPLE_DIR)
        except Exception as e:
            print(f"エラー：見本サーバーを起動できません。{e}\n直し方：別の見本サーバーを止めるか、Drive(G:) を確認してください。")
            return 2
        try:
            EXPECT = load_expect()
        except Exception as e:
            print(f"エラー：見本の説明.md を読めません（{e}）。見本フォルダを確認してください。")
            return 2

        from playwright.sync_api import sync_playwright
        external = []
        with sync_playwright() as p:
            prof = os.path.join(BASE, "chrome_profile_sample")
            ctx = p.chromium.launch_persistent_context(prof, channel="chrome", headless=True, viewport={"width": 1280, "height": 800})
            ctx.set_default_timeout(10000)
            ctx.on("request", lambda r: external.append(r.url) if not re.match(r"^(http://127\.0\.0\.1:8765/|data:|about:|blob:)", r.url) else None)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                A = "見本ページ"
                check(A, "全ページが開ける・必須の要素・外部通信なし", a_all_open, page, external)
                for n, label in (("article.html", "記事"), ("mixed.html", "日本語・英語・表")):
                    check(A, label, a_static, page, n)
                check(A, "次へリンク型（3ページ）", a_next_link, page)
                check(A, "ページ番号型（3ページ）", a_number, page)
                check(A, "次へボタン型（JS）", a_button, page)
                check(A, "無限スクロール型", a_scroll, page)
                check(A, "Cookie同意バナー", a_cookie, page, ctx)
                check(A, "遅延読み込み型", a_lazy, page)
                check(A, "コピー禁止型", a_protected, page)
                check(A, "表・見出し・リスト", a_mixed, page)

                # 部品を読み込む（無ければ None）
                parts = {}
                for key, fn in (("web", "web.py"), ("output", "output.py"), ("paginate", "paginate.py"), ("select", "range_select.py")):
                    try:
                        parts[key] = load_part(fn, "gamen_" + key)
                    except Exception as e:
                        parts[key] = None
                        record("部品の読み込み", fn, "不合格", f"{type(e).__name__}: {str(e)[:200]}（直し方：そのファイルの構文・import を確認）")
                paginate_tests(parts["paginate"], page)
                select_tests(parts["select"], page)
            finally:
                ctx.close()
        web_tests(parts["web"], parts["select"])
        output_tests(parts["output"])
        return 0
    finally:
        if srv is not None:
            try:
                mod.stop(srv)
            except Exception:
                pass
        shutil.rmtree(BASE, ignore_errors=True)   # 自分の一時フォルダだけを消す
        if os.path.exists(BASE):
            print(f"注意：一時フォルダを消しきれませんでした（{BASE}）。Chrome が残っていないか確認して手で消してください。")


if __name__ == "__main__":
    print("=== 画面PDF化アプリ（Web版）全体テスト ===")
    code = main()
    n = {k: sum(1 for r in RESULTS if r[2] == k) for k in ("合格", "不合格", "スキップ")}
    print(f"\n=== まとめ：合格 {n['合格']} ／ 不合格 {n['不合格']} ／ 未実装（スキップ） {n['スキップ']} ===")
    for g, nm, st, d in RESULTS:
        if st == "不合格":
            print(f"  不合格：{g} / {nm} … {d}")
    sys.exit(code or (1 if n["不合格"] else 0))
