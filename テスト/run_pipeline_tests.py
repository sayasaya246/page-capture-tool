# 流れ全体（pipeline.run）を、自作の見本ページで確かめる。保存先は %TEMP% の一時フォルダ（終わると消す）
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(HERE)
BASE = os.path.join(tempfile.gettempdir(), "画面PDF化アプリ_テスト", "全体の流れ")
os.makedirs(BASE, exist_ok=True)
CFG = os.path.join(BASE, "config.json")
with open(CFG, "w", encoding="utf-8") as f:
    json.dump({"作成者名": "テスト", "保存先": os.path.join(BASE, "成果物"),
               "サイト設定ファイル": os.path.join(BASE, "サイト設定.json"),
               "専用Chromeの場所": os.path.join(BASE, "chrome_profile"),
               "ページ待ち秒": 0.5, "最大ページ数": 10, "制限時間秒": 120}, f, ensure_ascii=False)
os.environ["GAMEN_PDF_CONFIG"] = CFG
import select, selectors, socketserver, http.server  # noqa: E401,F401  標準版を先に読む
sys.path.append(SCRIPT_DIR)

import fitz  # noqa: E402
import pipeline  # noqa: E402

URL = "http://127.0.0.1:8765/"
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("[合格] " if cond else "[不合格] ") + name + (f"  … {detail}" if detail else ""), flush=True)


server = subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(HERE, "見本サーバー.py")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(30):
        try:
            urllib.request.urlopen(URL + "article.html", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    pages = ["article.html", "next_link_1.html", "number_1.html", "button.html", "scroll.html",
             "cookie.html", "lazy.html", "protected.html"]
    res = pipeline.run([URL + p for p in pages], headless=True, esc=lambda: False)
    by = {r.url.replace(URL, ""): r for r in res}

    for p in ["article.html", "cookie.html", "lazy.html"]:
        r = by[p]
        check(f"{p}：取り込めた", r.ok and r.pages == 1 and len(r.files) >= 2, r.message)
    check("next_link_1.html：3ページたどれた", by["next_link_1.html"].ok and by["next_link_1.html"].pages == 3, by["next_link_1.html"].message)
    check("number_1.html：3ページたどれた", by["number_1.html"].ok and by["number_1.html"].pages == 3, by["number_1.html"].message)
    check("button.html：3ページたどれた", by["button.html"].ok and by["button.html"].pages == 3, by["button.html"].message)
    check("scroll.html：続きが出て終わった", by["scroll.html"].ok and by["scroll.html"].pages >= 1, by["scroll.html"].message)
    smd = next((f for f in by["scroll.html"].files if f.endswith(".md")), None)
    check("scroll.html：最後の「これで終わり」まで入っている", smd and "これで終わり" in open(smd, encoding="utf-8").read())
    check("protected.html：コピー禁止で止まった（突破しない）", not by["protected.html"].ok and "コピー" in by["protected.html"].message,
          by["protected.html"].message[:60])

    # 出力の中身を確認（3ページ型）
    r = by["next_link_1.html"]
    md = next((f for f in r.files if f.endswith(".md")), None)
    pdf = next((f for f in r.files if f.endswith(".pdf")), None)
    check("Markdown と PDF の名前がそろう", md and pdf and os.path.splitext(os.path.basename(md))[0] == os.path.splitext(os.path.basename(pdf))[0])
    if md:
        text = open(md, encoding="utf-8").read()
        check("Markdown に出典URL・取得日時・ページ見出し", "127.0.0.1:8765" in text and "取得日時" in text and "## ページ2" in text)
        check("Markdown に広告が混ざらない", "広告" not in text.replace("広告ではありません", ""), "")
    if pdf:
        d = fitz.open(pdf)
        pt = "".join(p.get_text() for p in d)
        check("PDF から文字が取り出せる・出典入り", len(pt) > 100 and "127.0.0.1" in pt, f"{len(d)}ページ")

    # 2回目：設定を保存して再現できるか（本文を指定・広告を除く）
    import range_select
    cfgd = json.load(open(CFG, encoding="utf-8"))
    range_select.save_rules(cfgd["サイト設定ファイル"], range_select.SiteRules(
        domain="127.0.0.1:8765", include_selector="#main-article"))
    res2 = pipeline.run([URL + "article.html"], headless=True, esc=lambda: False)
    check("保存したサイト設定で取り込める", res2[0].ok, res2[0].message)

    # 「次へ」の行き先判定：続きのページと、別の記事を見分ける
    lp = pipeline.looks_like_paging
    cases = [
        ("https://a.example/news?page=1", "https://a.example/news?page=2", True),
        ("https://a.example/post/a-1.html", "https://a.example/post/a-2.html", True),
        ("https://a.example/blog/story/", "https://a.example/blog/story/2/", True),
        ("https://a.example/list/page/2/", "https://a.example/list/page/3/", True),
        ("https://a.example/blog/gemma-4-skills/", "https://a.example/blog/closing-the-gap/", False),
        ("https://a.example/blog/x/", "https://other.example/blog/x/2/", False),
    ]
    bad_cases = [c for c in cases if lp(c[0], c[1]) != c[2]]
    check("続きのページと別の記事を見分ける（6例）", not bad_cases, str(bad_cases[:1]))

    # URLの検査：ファイルや javascript: は開かない
    bad = pipeline.run(["file:///C:/Windows/win.ini", "javascript:alert(1)", "ftp://example.com/a"],
                       headless=True, esc=lambda: False)
    check("file:// などは拒否される", len(bad) == 3 and all((not r.ok) and "http" in r.message for r in bad),
          bad[0].message[:50])

    # ファイル名に制御文字があっても作れる
    from common import next_output_path
    p = next_output_path(os.path.join(BASE, "名前テスト"), "題名\n改行\tタブ:記号", ".md")
    check("ファイル名に制御文字・記号が残らない", all(c not in os.path.basename(p) for c in "\n\t:"), os.path.basename(p))

    # 壊れたサイト設定でも、直し方が全文出る
    with open(cfgd["サイト設定ファイル"], "w", encoding="utf-8") as f:
        f.write("{壊れたJSON")
    res4 = pipeline.run([URL + "article.html"], headless=True, esc=lambda: False)
    check("壊れたサイト設定：原因と直し方が切れずに出る", not res4[0].ok and len(res4[0].message) > 40, res4[0].message[:80])
    os.remove(cfgd["サイト設定ファイル"])

    # Esc で止まる
    res3 = pipeline.run([URL + "article.html", URL + "next_link_1.html"], headless=True, esc=lambda: True)
    check("Esc で止まる", any("Esc" in r.message for r in res3))
finally:
    server.terminate()
    shutil.rmtree(BASE, ignore_errors=True)

print("\n=== まとめ：" + ("すべて合格" if ok_all else "不合格あり") + " ===")
