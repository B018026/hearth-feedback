#!/usr/bin/env python3
"""HS振り返りアプリ — ブラウザで操作するローカルアプリ(試作版)。

    python app.py            # サーバーを起動してブラウザを開く
    python app.py --no-browser --port 8765

自分のPCの中だけで動きます(127.0.0.1)。AIによる振り返りだけ、利用者自身のAPIキーでAnthropicのAPIを呼びます。
"""
import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import hsreview as hs

FROZEN = hs.FROZEN
APP_DIR = hs.HERE   # 同梱ファイル(web/ docs/ assets/)の場所。exe版では展開先
WEB_DIR = APP_DIR / "web"
DISCLAIMER_PATH = APP_DIR / "docs" / "DISCLAIMER.md"
SETTINGS_PATH = hs.DATA_DIR / "settings.json"
VERSION = "0.2.0"
# 免責の文面を変えたら、この日付を更新する(利用者に、もう一度同意を求める)
CONSENT_VERSION = "2026-10-18"
KEYRING_SERVICE = os.environ.get("HSREVIEW_KEYRING_SERVICE", "HSReview")   # テストでは別名にして、本物のキーに触れない
KEYRING_USER = "anthropic_api_key"

# 料金はUSD/100万トークン(2026-10時点の公開価格。画面の概算表示にだけ使う)
MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5(高精度・標準)", "in": 4.0, "out": 20.0},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5(バランス・約半額)", "in": 2.0, "out": 10.0},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5(最安・簡易な指摘)", "in": 0.10, "out": 0.50},
]
# 考える深さ(effort)の初期値。Haikuは考える量が多くなりやすいので、1段階軽くする
DEFAULT_EFFORT = {"claude-haiku-5-5": "medium"}
DEFAULT_SETTINGS = {"api_key": "", "model": MODELS[0]["id"], "memo": "", "logs_dir": "", "consent": ""}

SYSTEM_PROMPT = """あなたはハースストーンの上級者コーチです。プレイヤー本人が、終わった試合を後から振り返るための記録を読み、具体的な改善点を指摘します。

守ること:
- 根拠は記録の中の情報だけにします。カードの効果は、記録末尾の「効果テキスト辞典」と「キーワード」の説明に従い、記憶で補いません。分からない用語や効果は、分からないと書きます。
- 記録に残るのは、プレイヤーに見えていた情報だけです。相手の手札やデッキは、使われた分しか分かりません。結果を知ってからの後出しの批判は避け、その時点で得られた情報で判断します。
- 「仕込み」は、残りマナを使ってカードのコストを下げる準備行動です。マナを余らせていても無駄とは判断しません(統計表の「仕込み」列を見ます)。
- 改善点には必ずターン番号(T○)を書き、「何をしたか → 何ができたか → なぜその方が良いか」を示します。確信が持てないものは「可能性」と書きます。
- 良かった点も挙げます。プレイヤーが説明したデッキの狙いがあれば、それを前提にします。

出力は日本語のMarkdownで、次の構成にします。
## 総評(勝敗を分けた場面を1〜3つ)
## 改善点(重要度順に最大5つ)
## 良かった点
## 次の試合で試すこと(2〜3つ)"""


# ------------------------------------------------------------ 設定
def load_settings():
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return s


def save_settings(s):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:  # noqa: BLE001
        return None


def key_store_kind():
    """APIキーの保管場所: 'keyring'(Windowsの資格情報マネージャー) か 'file'(設定ファイルに平文)。"""
    kr = _keyring()
    if kr:
        try:
            kr.get_password(KEYRING_SERVICE, KEYRING_USER)
            return "keyring"
        except Exception:  # noqa: BLE001
            pass
    return "file"


def stored_key():
    kr = _keyring()
    if kr:
        try:
            v = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
    return load_settings().get("api_key", "")


def store_key(key):
    kr = _keyring()
    if kr:
        try:
            kr.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            if kr.get_password(KEYRING_SERVICE, KEYRING_USER) == key:  # 読み戻して確認できたときだけ、ファイルの控えを消す
                s = load_settings()
                s["api_key"] = ""
                save_settings(s)
                return "keyring"
        except Exception:  # noqa: BLE001
            pass
    s = load_settings()
    s["api_key"] = key
    save_settings(s)
    return "file"


def clear_key():
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:  # noqa: BLE001
            pass
    s = load_settings()
    s["api_key"] = ""
    save_settings(s)


def migrate_key():
    """旧方式(設定ファイルに平文)で保存されたキーを、資格情報マネージャーへ移す。"""
    s = load_settings()
    if s.get("api_key"):
        store_key(s["api_key"])


def api_key():
    return stored_key() or os.environ.get("ANTHROPIC_API_KEY") or ""


def consent_ok():
    return load_settings().get("consent") == CONSENT_VERSION


def public_settings():
    s = load_settings()
    key = api_key()
    return {"model": s["model"], "memo": s["memo"], "logs_dir": s["logs_dir"],
            "api_key_set": bool(key), "api_key_tail": key[-4:] if key else "", "key_store": key_store_kind()}


# ------------------------------------------------------------ ログの場所と設定
def find_logs_dir():
    s = load_settings()
    cands = [s.get("logs_dir")] if s.get("logs_dir") else []
    for root in ("C:\\Program Files (x86)", "C:\\Program Files", "D:", "E:", "D:\\Games", "C:\\Games"):
        cands.append(str(Path(root) / "Hearthstone" / "Logs"))
    for c in cands:
        if c and Path(c).exists():
            return str(c)
    return s.get("logs_dir") or str(hs.DEFAULT_LOGS)


def logconfig_path():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Blizzard" / "Hearthstone" / "log.config"


POWER_SECTION = "[Power]\nLogLevel=1\nFilePrinting=true\nConsolePrinting=false\nScreenPrinting=false\nVerbose=true\n"


def logconfig_ok():
    p = logconfig_path()
    if not p.exists():
        return False
    txt = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\[Power\](.*?)(?=^\[|\Z)", txt, re.S | re.M)
    return bool(m and re.search(r"FilePrinting\s*=\s*true", m.group(1), re.I))


def logconfig_fix():
    """log.config に [Power] の出力設定を入れる。既存ファイルは .bak に控えを残す。"""
    p = logconfig_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    txt = ""
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="replace")
        bak = p.with_suffix(".config.bak")
        if not bak.exists():
            bak.write_text(txt, encoding="utf-8")
        txt = re.sub(r"\[Power\].*?(?=^\[|\Z)", "", txt, flags=re.S | re.M).rstrip() + "\n\n"
    p.write_text(txt + POWER_SECTION, encoding="utf-8")


# ------------------------------------------------------------ 取り込み(同期)
SYNC = {"running": False, "log": [], "new": 0, "error": None}


def sync_worker():
    SYNC.update(running=True, log=[], new=0, error=None)
    try:
        logs = find_logs_dir()
        SYNC["log"].append(f"ログフォルダ: {logs}")
        SYNC["new"] = hs.sync_games(logs, log=lambda m: SYNC["log"].append(m))
    except Exception as ex:  # noqa: BLE001
        SYNC["error"] = f"{type(ex).__name__}: {ex}"
    finally:
        SYNC["running"] = False


# ------------------------------------------------------------ AIによる振り返り
JOBS = {}
FAKE_TEXT = """## 総評
(これは動作確認用のダミーです。実際のAIは呼び出していません)

- T6: ここが勝敗の分かれ目です。
## 改善点
1. **T4**: 手札にあるカードで盤面を取れたのに、ヒーローパワーだけで終えています。
## 良かった点
- T8 の判断は妥当でした。
## 次の試合で試すこと
- 序盤の盤面を優先する。
"""


def analysis_path(gid):
    return hs.GAMES_DIR / f"{gid}.analysis.json"


def analysis_worker(gid, job):
    try:
        md = (hs.GAMES_DIR / f"{gid}.md").read_text(encoding="utf-8")
        s = load_settings()
        model = s["model"]
        if os.environ.get("HSREVIEW_FAKE_AI"):
            for ch in FAKE_TEXT:
                job["text"] += ch
                time.sleep(0.004)
            job.update(status="done", model="(ダミー)", input_tokens=0, output_tokens=0)
        else:
            try:
                import anthropic
            except ImportError:
                raise RuntimeError("anthropic パッケージが入っていません。`python -m pip install anthropic` を実行してください。")
            client = anthropic.Anthropic(api_key=api_key())
            notes = f"\n\n<player_notes>\n{s['memo'].strip()}\n</player_notes>\n(上はプレイヤー本人のメモです。デッキの狙いなどを前提にしてください。)" if s["memo"].strip() else ""
            user = ("以下は、私が遊んだハースストーンのランク戦1試合の記録です。後から振り返るために、改善点を洗い出してください。\n\n"
                    f"<game_record>\n{md}\n</game_record>{notes}")
            # 考える深さ(effort)は、モデルごとの既定から始める。出力の上限は「考える分」も含むため、
            # 回答が空のまま上限に達した場合は、より軽い設定で1回だけ自動で再試行する。
            first = DEFAULT_EFFORT.get(model, "high")
            efforts = [first] + ([e for e in ("low",) if e != first])
            total_in = total_out = 0
            final = None
            for attempt, effort in enumerate(efforts):
                job["text"] = ""
                with client.messages.stream(
                    model=model, max_tokens=32000, system=SYSTEM_PROMPT,
                    output_config={"effort": effort},
                    messages=[{"role": "user", "content": user}],
                ) as stream:
                    for text in stream.text_stream:
                        job["text"] += text
                    final = stream.get_final_message()
                total_in += final.usage.input_tokens
                total_out += final.usage.output_tokens
                if final.stop_reason == "refusal":
                    raise RuntimeError("AI側の安全機能により、回答が拒否されました。")
                if not (final.stop_reason == "max_tokens" and not job["text"].strip()):
                    break
            if not job["text"].strip():
                raise RuntimeError(
                    "AIが回答を書き終える前に、出力の上限に達しました(出力 %d トークン)。"
                    "別のモデル(SonnetやOpus)でお試しください。" % total_out)
            if final.stop_reason == "max_tokens":
                job["text"] += "\n\n(出力が上限に達したため、ここまでが得られた結果です)"
            job.update(status="done", model=model, input_tokens=total_in, output_tokens=total_out)
        analysis_path(gid).write_text(json.dumps({
            "text": job["text"], "model": job.get("model"), "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "input_tokens": job.get("input_tokens"), "output_tokens": job.get("output_tokens"),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception as ex:  # noqa: BLE001
        job.update(status="error", error=friendly_error(ex))


def friendly_error(ex):
    name = type(ex).__name__
    msg = getattr(ex, "message", None) or str(ex)
    if name == "RuntimeError":  # こちらで文面を用意したエラーは、そのまま表示する
        return msg
    if name == "AuthenticationError":
        return "APIキーが正しくありません。設定で入力し直してください。"
    if name == "PermissionDeniedError":
        return "このAPIキーには、この操作の権限がありません。"
    if name == "RateLimitError":
        return "短時間にリクエストが集中しています。しばらく待ってからもう一度お試しください。"
    if name == "APIConnectionError":
        return "インターネットに接続できませんでした。接続を確認してください。"
    if name == "NotFoundError":
        return f"指定したモデルが見つかりません。設定でモデルを選び直してください。({msg})"
    if name == "BadRequestError":
        if "credit" in msg.lower():
            return "APIの残高(クレジット)が不足しています。Anthropicのコンソールでチャージしてください。"
        return f"リクエストが受け付けられませんでした: {msg}"
    return f"{name}: {msg}"


# ------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "HSReview/" + VERSION

    def log_message(self, *args):  # アクセスログは出さない
        pass

    # --- 応答ヘルパ
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, ctype="text/plain; charset=utf-8", code=200):
        body = text.encode("utf-8") if isinstance(text, str) else text
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost")

    # --- ルーティング
    def do_GET(self):
        if not self.host_ok():
            return self.send_text("forbidden", code=403)
        path = unquote(urlparse(self.path).path)
        try:
            if path.startswith("/api/"):
                return self.api_get(path)
            return self.static(path)
        except Exception as ex:  # noqa: BLE001
            return self.send_json({"error": f"{type(ex).__name__}: {ex}"}, 500)

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-HS-App") != "1":
            return self.send_text("forbidden", code=403)
        path = unquote(urlparse(self.path).path)
        try:
            return self.api_post(path, self.body_json())
        except Exception as ex:  # noqa: BLE001
            return self.send_json({"error": f"{type(ex).__name__}: {ex}"}, 500)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        if path == "/favicon.ico":   # タブ・タスクバーのアイコン(アプリのアイコンと共通)
            f = APP_DIR / "assets" / "icon.ico"
        else:
            f = (WEB_DIR / path.lstrip("/")).resolve()
            if WEB_DIR.resolve() not in f.parents:
                return self.send_text("not found", code=404)
        if not f.is_file():
            return self.send_text("not found", code=404)
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_text(f.read_bytes(), ctype)

    def game_id(self, path, suffix=""):
        m = re.match(r"^/api/game/(\d{8}-\d{6})" + re.escape(suffix) + "$", path)
        return m.group(1) if m else None

    def api_get(self, path):
        if path == "/api/ping":
            return self.send_json({"app": "hsreview", "version": VERSION})
        if path == "/api/disclaimer":
            return self.send_text(DISCLAIMER_PATH.read_text(encoding="utf-8") if DISCLAIMER_PATH.exists() else "")
        if path == "/api/state":
            idx = hs.load_index()
            games = []
            for gid in sorted(idx, reverse=True):
                s = dict(idx[gid])
                s.pop("hp_log", None)
                s["id"] = gid
                s["analyzed"] = analysis_path(gid).exists()
                games.append(s)
            logs = find_logs_dir()
            return self.send_json({
                "version": VERSION, "games": games, "settings": public_settings(), "models": MODELS,
                "system_prompt": SYSTEM_PROMPT, "consent": {"ok": consent_ok(), "version": CONSENT_VERSION},
                "env": {"logs_dir": logs, "logs_exist": Path(logs).exists(),
                        "logconfig_ok": logconfig_ok(), "fake_ai": bool(os.environ.get("HSREVIEW_FAKE_AI"))},
            })
        if path == "/api/sync/status":
            return self.send_json(SYNC)
        gid = self.game_id(path)
        if gid:
            f = hs.GAMES_DIR / f"{gid}.json"
            if not f.exists():
                return self.send_json({"error": "試合が見つかりません"}, 404)
            return self.send_text(f.read_bytes(), "application/json; charset=utf-8")
        gid = self.game_id(path, "/md")
        if gid:
            return self.send_text((hs.GAMES_DIR / f"{gid}.md").read_text(encoding="utf-8"))
        gid = self.game_id(path, "/analysis")
        if gid:
            p = analysis_path(gid)
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            return self.send_json({"saved": data, "job": JOBS.get(gid)})
        return self.send_json({"error": "not found"}, 404)

    def api_post(self, path, body):
        if path == "/api/consent":
            if body.get("version") != CONSENT_VERSION:
                return self.send_json({"error": "文面が更新されています。画面を再読み込みしてください。"}, 400)
            s = load_settings()
            s["consent"] = CONSENT_VERSION
            save_settings(s)
            return self.send_json({"ok": True})
        if path in ("/api/sync", "/api/logconfig/fix") or self.game_id(path, "/analyze"):
            if not consent_ok():
                return self.send_json({"error": "先に「ご利用にあたって」への同意が必要です。"}, 403)
        if path == "/api/sync":
            if not SYNC["running"]:
                threading.Thread(target=sync_worker, daemon=True).start()
            return self.send_json({"ok": True})
        if path == "/api/settings":
            if "api_key" in body:
                if body["api_key"] == "__clear__":
                    clear_key()
                elif body["api_key"].strip():
                    store_key(body["api_key"].strip())
            s = load_settings()
            for k in ("model", "memo", "logs_dir"):
                if k in body and isinstance(body[k], str):
                    s[k] = body[k].strip() if k != "memo" else body[k]
            if s["model"] not in [m["id"] for m in MODELS]:
                s["model"] = MODELS[0]["id"]
            save_settings(s)
            return self.send_json({"ok": True, "settings": public_settings()})
        if path == "/api/logconfig/fix":
            logconfig_fix()
            return self.send_json({"ok": True, "logconfig_ok": logconfig_ok()})
        gid = self.game_id(path, "/analyze")
        if gid:
            if not (hs.GAMES_DIR / f"{gid}.md").exists():
                return self.send_json({"error": "試合が見つかりません"}, 404)
            if not api_key() and not os.environ.get("HSREVIEW_FAKE_AI"):
                return self.send_json({"error": "APIキーが未設定です。右上の「設定」から入力してください。"}, 400)
            job = JOBS.get(gid)
            if not job or job["status"] != "running":
                job = {"status": "running", "text": "", "error": None, "started": time.time()}
                JOBS[gid] = job
                threading.Thread(target=analysis_worker, args=(gid, job), daemon=True).start()
            return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    allow_reuse_address = False   # Windowsでは、使用中のポートに重ねて起動できてしまうため、無効にする
    daemon_threads = True


def open_server(start):
    """空いているポートでサーバーを作る。すでにこのアプリが動いているポートがあれば (None, そのポート) を返す。
    (使われていないポートへ接続して確かめると、Windowsでは1ポートごとに約1秒かかるため、確保できるかで判定する)"""
    for port in range(start, start + 20):
        try:
            return Server(("127.0.0.1", port), Handler), port
        except OSError:
            if is_ours(port):
                return None, port
    raise RuntimeError("使えるポートが見つかりません")


def browser_exe():
    """専用ウィンドウ(アプリモード)で開ける、Edge または Chrome の場所。"""
    local = os.environ.get("LOCALAPPDATA", "")
    for c in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe")):
        if c and Path(c).exists():
            return c
    return None


def open_ui(url):
    """アドレスバーのない専用ウィンドウ(Edge/Chromeのアプリモード)で開く。使えなければ、通常のブラウザで開く。
    専用のプロフィール(%APPDATA%\\HSReview\\browser)を使うので、普段のブラウザの設定や履歴には触れない。"""
    if not os.environ.get("HSREVIEW_NORMAL_BROWSER"):
        exe = browser_exe()
        if exe:
            profile = os.environ.get("HSREVIEW_BROWSER_PROFILE") or str(hs.DATA_DIR / "browser")
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1360,900", f"--user-data-dir={profile}",
                                  "--no-first-run", "--no-default-browser-check"], close_fds=True)
                return
            except Exception:  # noqa: BLE001
                pass
    webbrowser.open(url)


def say(*a):
    try:
        print(*a)
    except Exception:  # noqa: BLE001  (exe版にはコンソールがない)
        pass


def is_ours(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "hsreview"
    except Exception:  # noqa: BLE001
        return False


def run_window(url, srv):
    """exe版の小さな操作ウィンドウ。閉じるとアプリが終了する。"""
    import tkinter as tk
    root = tk.Tk()
    root.title("HS振り返り")
    root.geometry("380x170")
    root.resizable(False, False)
    ico = APP_DIR / "assets" / "icon.ico"
    if ico.exists():
        try:
            root.iconbitmap(str(ico))
        except Exception:  # noqa: BLE001
            pass
    tk.Label(root, text="HS振り返りを起動しています", font=("Yu Gothic UI", 11, "bold")).pack(pady=(16, 2))
    tk.Label(root, text=url, fg="#555").pack()
    row = tk.Frame(root)
    row.pack(pady=12)
    tk.Button(row, text="画面を開く", width=14, command=lambda: open_ui(url)).pack(side="left", padx=6)
    tk.Button(row, text="終了", width=10, command=root.destroy).pack(side="left", padx=6)
    tk.Label(root, text="このウィンドウを閉じると、アプリが終了します。", fg="#777").pack()
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    srv.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--gui", action="store_true", help="操作ウィンドウを出す(exe版は常に出す)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    no_browser = args.no_browser or bool(os.environ.get("HSREVIEW_NO_BROWSER"))
    migrate_key()
    srv, port = open_server(args.port)
    if srv is None:  # すでに起動している → ブラウザだけ開いて終わる
        say("すでに起動しています")
        if not no_browser:
            open_ui(f"http://127.0.0.1:{port}/")
        return
    url = f"http://127.0.0.1:{port}/"
    say(f"HS振り返りアプリを起動しました: {url}  (終了: Ctrl+C)")
    if not no_browser:
        threading.Timer(0.6, lambda: open_ui(url)).start()
    gui = (args.gui or FROZEN) and not os.environ.get("HSREVIEW_HEADLESS")
    if gui:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        run_window(url, srv)
    else:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        log = hs.DATA_DIR / "app.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        if FROZEN:
            try:
                import tkinter
                import tkinter.messagebox as mb
                r = tkinter.Tk()
                r.withdraw()
                mb.showerror("HS振り返り", f"起動に失敗しました。\n詳細は次のファイルを見てください。\n{log}")
            except Exception:  # noqa: BLE001
                pass
        raise
