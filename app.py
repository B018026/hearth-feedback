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
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import hsreview as hs

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
SETTINGS_PATH = hs.DATA_DIR / "settings.json"
VERSION = "0.1-prototype"

# 料金はUSD/100万トークン(2026-10時点の公開価格。画面の概算表示にだけ使う)
MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5(高精度・標準)", "in": 4.0, "out": 20.0},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5(バランス・約半額)", "in": 2.0, "out": 10.0},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5(最安・簡易な指摘)", "in": 0.10, "out": 0.50},
]
# 考える深さ(effort)の初期値。Haikuは考える量が多くなりやすいので、1段階軽くする
DEFAULT_EFFORT = {"claude-haiku-5-5": "medium"}
DEFAULT_SETTINGS = {"api_key": "", "model": MODELS[0]["id"], "memo": "", "logs_dir": ""}

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


def api_key():
    return load_settings().get("api_key") or os.environ.get("ANTHROPIC_API_KEY") or ""


def public_settings():
    s = load_settings()
    key = api_key()
    return {"model": s["model"], "memo": s["memo"], "logs_dir": s["logs_dir"],
            "api_key_set": bool(key), "api_key_tail": key[-4:] if key else ""}


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
        f = (WEB_DIR / path.lstrip("/")).resolve()
        if WEB_DIR.resolve() not in f.parents or not f.is_file():
            return self.send_text("not found", code=404)
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_text(f.read_bytes(), ctype)

    def game_id(self, path, suffix=""):
        m = re.match(r"^/api/game/(\d{8}-\d{6})" + re.escape(suffix) + "$", path)
        return m.group(1) if m else None

    def api_get(self, path):
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
                "system_prompt": SYSTEM_PROMPT,
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
        if path == "/api/sync":
            if not SYNC["running"]:
                threading.Thread(target=sync_worker, daemon=True).start()
            return self.send_json({"ok": True})
        if path == "/api/settings":
            s = load_settings()
            if "api_key" in body:
                if body["api_key"] == "__clear__":
                    s["api_key"] = ""
                elif body["api_key"].strip():
                    s["api_key"] = body["api_key"].strip()
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


def pick_port(start):
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("使えるポートが見つかりません")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    port = pick_port(args.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"HS振り返りアプリを起動しました: {url}  (終了: Ctrl+C)")
    if not args.no_browser and not os.environ.get("HSREVIEW_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
