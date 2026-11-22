# 画面PDF化アプリ（Web版）：出力（Markdown と 検索できるPDF を作る）
import html as _html
import io
import os
import re
import shutil
import tempfile

import pymupdf
from PIL import Image

from common import Document, next_output_path

# 日本語フォントの候補（通常 / 太字）。上から順に、あるものを使う
_FONT_CANDIDATES = [
    ("YuGothR.ttc", "YuGothB.ttc"),
    ("meiryo.ttc", "meiryob.ttc"),
    ("BIZ-UDGothicR.ttc", "BIZ-UDGothicB.ttc"),
    ("msgothic.ttc", "msgothic.ttc"),
]

_CSS = """
body { font-family: jp; font-size: 10.5pt; line-height: 1.5; }
h1 { font-size: 18pt; margin-top: 10pt; }
h2 { font-size: 15pt; margin-top: 10pt; }
h3 { font-size: 13pt; margin-top: 8pt; }
h4, h5, h6 { font-size: 11.5pt; margin-top: 6pt; }
p { margin-top: 3pt; margin-bottom: 3pt; }
table { border-collapse: collapse; margin-top: 4pt; margin-bottom: 4pt; }
th, td { border: 0.5pt solid #666666; padding: 2pt 4pt; }
th { background-color: #e8e8e8; }
code, pre { font-family: jp; background-color: #f0f0f0; }
blockquote { margin-left: 12pt; color: #444444; }
a { color: #0645ad; }
.meta { font-size: 9pt; color: #333333; }
"""


class OutputError(Exception):
    """出力に失敗した理由（日本語で、原因と直し方つき）"""


# ---------- 準備 ----------
def _find_font():
    """使える日本語フォントを探し、(通常のパス, 太字のパス) を返す"""
    d = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for reg, bold in _FONT_CANDIDATES:
        r = os.path.join(d, reg)
        if os.path.exists(r):
            b = os.path.join(d, bold)
            return r, (b if os.path.exists(b) else r)
    raise OutputError(
        "PDFに使う日本語フォント（游ゴシック・メイリオなど）が見つかりません。"
        "Windows の「設定→個人用設定→フォント」で日本語フォントを入れてから、もう一度実行してください。"
    )


def _check_out_dir(out_dir):
    if not out_dir:
        raise OutputError("保存先が指定されていません。設定ファイルの「保存先」にフォルダを書いてください。")
    try:
        os.makedirs(out_dir, exist_ok=True)
        probe = os.path.join(out_dir, ".書き込み確認.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise OutputError(
            f"保存先「{out_dir}」に書き込めません（{e.strerror or e}）。"
            "Google ドライブが開いているか、フォルダの場所・権限を確認し、"
            "設定ファイルの「保存先」を直してください。"
        ) from e


# ---------- Markdown → HTML（簡易） ----------
def _inline(text):
    """行の中の記法（太字・斜体・コード・リンク）をHTMLにする"""
    codes = []

    def keep_code(m):
        codes.append("<code>" + _html.escape(m.group(1)) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    t = re.sub(r"`([^`]+)`", keep_code, text)
    t = _html.escape(t, quote=False)
    t = re.sub(r"!\[([^\]]*)\]\(([^)\s]*)[^)]*\)", lambda m: m.group(1) and f"[画像：{m.group(1)}]" or "", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*)?\)",
               lambda m: f'<a href="{_html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], t)


def _split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def md_to_html(md):
    """簡単な Markdown を HTML にする（見出し・リスト・表・太字・リンク・引用・コード）"""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    para = []

    def flush():
        if para:
            out.append("<p>" + "<br/>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            flush(); i += 1; continue
        if s.startswith("```"):  # コードブロック
            flush(); buf = []; i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            out.append("<pre>" + _html.escape("\n".join(buf)) + "</pre>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", s)
        if m:
            flush(); k = len(m.group(1))
            out.append(f"<h{k}>{_inline(m.group(2))}</h{k}>"); i += 1; continue
        if re.match(r"^([-*_])(\s*\1){2,}$", s):
            flush(); out.append("<hr/>"); i += 1; continue
        if "|" in s and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            head = _split_row(s); i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i])); i += 1
            h = "<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>"
            for r in rows:
                r = (r + [""] * len(head))[:max(len(head), len(r))]
                h += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
            out.append(h + "</table>")
            continue
        if s.startswith(">"):
            flush(); buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            out.append("<blockquote>" + md_to_html("\n".join(buf)) + "</blockquote>")
            continue
        m = _LIST_ITEM.match(line)
        if m:
            flush()
            # リスト：字下げで入れ子を作る
            stack = []  # (字下げ, タグ)
            html_l = ""
            while i < n:
                m = _LIST_ITEM.match(lines[i])
                if not m:
                    # 字下げされた続きの行は直前の項目に足す
                    if lines[i].strip() and lines[i].startswith(" ") and html_l.endswith("</li>"):
                        html_l = html_l[:-5] + "<br/>" + _inline(lines[i].strip()) + "</li>"
                        i += 1; continue
                    break
                ind = len(m.group(1).replace("\t", "    "))
                tag = "ol" if m.group(2)[0].isdigit() else "ul"
                while stack and ind < stack[-1][0]:
                    html_l += f"</{stack.pop()[1]}>"
                if stack and ind == stack[-1][0] and stack[-1][1] != tag:  # 同じ深さで種類が変わった
                    html_l += f"</{stack.pop()[1]}>"
                if not stack or ind > stack[-1][0]:
                    stack.append((ind, tag)); html_l += f"<{tag}>"
                html_l += f"<li>{_inline(m.group(3))}</li>"
                i += 1
            while stack:
                html_l += f"</{stack.pop()[1]}>"
            out.append(html_l)
            continue
        para.append(s); i += 1
    flush()
    return "\n".join(out)


# ---------- Markdown ----------
def _header_md(doc, author):
    return (
        f"# {doc.title}\n\n"
        f"- 出典URL：{doc.source_url}\n"
        f"- 取得日時：{doc.fetched_at:%Y-%m-%d %H:%M}\n"
        f"- 作成者：{author}\n"
        f"- ページ数：{len(doc.pages)}\n\n"
    )


def _body_md(doc):
    parts = []
    for idx, p in enumerate(doc.pages, 1):
        if idx >= 2:
            parts.append(f"## ページ{idx}（{p.url}）\n\n")
        parts.append(p.markdown.rstrip() + "\n\n")
    return "".join(parts)


def _split_text(text, limit):
    """limit 文字ごとに、段落の切れ目でなるべく分ける"""
    if limit <= 0 or len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        para += "\n\n"
        while len(para) > limit:  # 1段落が長すぎるときは強制的に切る
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(para[:limit]); para = para[limit:]
        if len(cur) + len(para) > limit and cur:
            chunks.append(cur); cur = ""
        cur += para
    if cur.strip():
        chunks.append(cur)
    return chunks


def _write_markdown(doc, author, base, split_chars):
    """base は拡張子なしのパス。作ったパスの一覧を返す"""
    head = _header_md(doc, author)
    body = _body_md(doc)
    chunks = _split_text(body, split_chars - len(head) if split_chars > len(head) + 1000 else split_chars)
    paths = []
    if len(chunks) == 1:
        targets = [(base + ".md", head + chunks[0])]
    else:
        targets = []
        for k, c in enumerate(chunks, 1):
            h = head if k == 1 else f"# {doc.title}（分割{k}／{len(chunks)}）\n\n- 出典URL：{doc.source_url}\n\n"
            targets.append((f"{base}_分割{k}.md", h + c))
    for path, text in targets:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        paths.append(path)
    return paths


# ---------- PDF ----------
def _footer(doc, author):
    return f"作成者：{author}　日時：{doc.fetched_at:%Y-%m-%d %H:%M}　出典：{doc.source_url}"


def _write_pdf(doc, author, path, with_screenshot):
    reg, bold = _find_font()
    tmp = tempfile.mkdtemp(prefix="gamenpdf_font_")
    try:
        # ttc は短い名前でコピーして @font-face から読む
        shutil.copy(reg, os.path.join(tmp, "jp_r.ttc"))
        shutil.copy(bold, os.path.join(tmp, "jp_b.ttc"))
        css = ("@font-face{font-family:jp;src:url(jp_r.ttc);}"
               "@font-face{font-family:jp;font-weight:bold;src:url(jp_b.ttc);}" + _CSS)
        body = [f"<h1>{_html.escape(doc.title)}</h1>",
                f'<p class="meta">出典URL：{_html.escape(doc.source_url)}<br/>'
                f'取得日時：{doc.fetched_at:%Y-%m-%d %H:%M}<br/>作成者：{_html.escape(author)}'
                f'<br/>ページ数：{len(doc.pages)}</p>']
        for idx, p in enumerate(doc.pages, 1):
            if idx >= 2:
                body.append(f"<h2>ページ{idx}（{_html.escape(p.url)}）</h2>")
            body.append(md_to_html(p.markdown))
        story = pymupdf.Story("\n".join(body), user_css=css, archive=pymupdf.Archive(tmp))

        media = pymupdf.paper_rect("a4")
        where = media + (40, 40, -40, -60)  # 下に余白（フッター用）
        tmp_pdf = os.path.join(tmp, "body.pdf")
        writer = pymupdf.DocumentWriter(tmp_pdf)
        more, guard = 1, 0
        while more:
            dev = writer.begin_page(media)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
            guard += 1
            if guard > 5000:
                raise OutputError("PDFのページ数が多すぎます。ページ数の上限を下げて、もう一度実行してください。")
        writer.close()

        pdf = pymupdf.open(tmp_pdf)
        # 画面の写真ページ（任意）
        if with_screenshot:
            for idx, p in enumerate(doc.pages, 1):
                if not p.screenshot_png:
                    continue
                pg = pdf.new_page(width=media.width, height=media.height)
                pg.insert_textbox(pymupdf.Rect(40, 20, media.width - 40, 40),
                                  f"画面の写真 ページ{idx}", fontname="jp", fontfile=reg, fontsize=9)
                try:
                    im = Image.open(io.BytesIO(p.screenshot_png)).convert("RGB")
                except Exception:
                    continue
                box = pymupdf.Rect(40, 45, media.width - 40, media.height - 60)
                # 縦に長い写真は複数ページに分ける
                scale = box.width / im.width
                slice_h = max(1, int(box.height / scale))
                y = 0
                first = True
                while y < im.height:
                    h = min(slice_h, im.height - y)
                    buf = io.BytesIO()
                    im.crop((0, y, im.width, y + h)).save(buf, "PNG")
                    if not first:
                        pg = pdf.new_page(width=media.width, height=media.height)
                    r = pymupdf.Rect(box.x0, box.y0, box.x1, box.y0 + h * scale)
                    pg.insert_image(r, stream=buf.getvalue())
                    y += h
                    first = False
        # 全ページの下に作成者・日時・出典URL
        text = _footer(doc, author)
        if len(text) > 300:
            text = text[:297] + "…"  # 長すぎるURLでも、作成者・日時が消えないように切り詰める
        total = len(pdf)
        for k, pg in enumerate(pdf, 1):
            pg.insert_font(fontname="jp", fontfile=reg)
            r = pymupdf.Rect(40, media.height - 50, media.width - 40, media.height - 12)
            for size in (7.5, 6.5, 5.5, 4.5):  # 枠に入らなければ文字を小さくする（入らないと何も描かれないため）
                if pg.insert_textbox(r, f"{text}　（{k}/{total}）", fontname="jp", fontsize=size,
                                     color=(0.25, 0.25, 0.25)) >= 0:
                    break
        try:
            pdf.subset_fonts()  # 使った文字だけをフォントに残して小さくする（fonttools が必要）
        except Exception:
            pass  # 失敗しても PDF は作れる（大きいだけ）
        pdf.save(path, garbage=3, deflate=True)
        pdf.close()
    except OutputError:
        raise
    except OSError as e:
        raise OutputError(f"PDFを書き込めませんでした（{e.strerror or e}）。保存先の空き容量と権限を確認してください。") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 入口 ----------
def write_outputs(doc: Document, out_dir: str, author: str, with_screenshot: bool = False,
                  split_chars: int = 200000, make_pdf: bool = True) -> list[str]:
    """Markdown と 検索できるPDF を out_dir に作り、作ったファイルのパスの一覧を返す"""
    if not doc.pages:
        raise OutputError("取り込んだページがありません。URLを確認して、もう一度取り込んでください。")
    _check_out_dir(out_dir)
    # 先に番号を決め、同じ番号で2つ作る
    md_path = next_output_path(out_dir, doc.title, ".md")
    base = md_path[:-3]
    created = []
    try:
        created += _write_markdown(doc, author, base, split_chars)
        if make_pdf:
            pdf_path = base + ".pdf"
            _write_pdf(doc, author, pdf_path, with_screenshot)
            created.append(pdf_path)
    except OSError as e:
        raise OutputError(f"ファイルを書き込めませんでした（{e.strerror or e}）。保存先の権限と空き容量を確認してください。") from e
    return created
