# 見本サーバー：見本フォルダを 127.0.0.1:8765 だけで配る（外には公開しない）
# 単体で起動：python -X utf8 見本サーバー.py   （Ctrl+C で止まる）
import functools
import http.server
import os
import sys
import threading

HOST = "127.0.0.1"   # 自分のPCだけ。0.0.0.0 にはしない
PORT = 8765
_G = r"G:\マイドライブ\画面PDF化アプリ\見本"
_C = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "見本")  # 画面PDF化アプリ\見本（G:\マイドライブ が使えないときの Drive の写し）
SAMPLE_DIR = os.environ.get("GAMEN_PDF_SAMPLES", _G if os.path.isdir(_G) else _C)


class _Handler(http.server.SimpleHTTPRequestHandler):
    def guess_type(self, path):
        t = super().guess_type(path)
        # 日本語が文字化けしないよう charset を付ける
        if t.startswith("text/") and "charset" not in t:
            t += "; charset=utf-8"
        return t

    def log_message(self, *a):   # アクセスログは出さない
        pass


def make_server(port=PORT, directory=SAMPLE_DIR):
    if not os.path.isdir(directory):
        raise FileNotFoundError(
            f"見本フォルダが見つかりません：{directory}\n"
            "直し方：Drive（G:）が開けているか確認するか、環境変数 GAMEN_PDF_SAMPLES に見本フォルダを指定してください。")
    handler = functools.partial(_Handler, directory=directory)
    try:
        return http.server.ThreadingHTTPServer((HOST, port), handler)
    except OSError as e:
        raise OSError(
            f"ポート {port} を使えません（{e}）。\n"
            "原因：別の見本サーバーがすでに動いている可能性があります。直し方：そのウィンドウを Ctrl+C で止めてからやり直してください。") from e


def start_background(port=PORT, directory=SAMPLE_DIR):
    """テスト用：裏で起動して (サーバー, スレッド) を返す。止めるには stop(サーバー)"""
    srv = make_server(port, directory)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, th


def stop(srv):
    srv.shutdown()
    srv.server_close()


if __name__ == "__main__":
    try:
        srv = make_server()
    except (OSError, FileNotFoundError) as e:
        print("エラー：", e)
        sys.exit(1)
    print(f"見本サーバーを起動しました：http://{HOST}:{PORT}/article.html  （止めるには Ctrl+C）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n止めました。")
    finally:
        srv.server_close()
