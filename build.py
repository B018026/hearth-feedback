"""Windows用のexe(HSReview.exe)を作る。

    python -m pip install -r requirements.txt pyinstaller pillow
    python build.py                  # 出力先: %LOCALAPPDATA%\\HSReviewBuild\\dist\\HSReview
    python build.py --out D:\\work    # 出力先を変える

できあがったフォルダを、そのまま配布できます(Pythonは不要)。
インストーラーを作る場合は、続けて installer\\HSReview.iss を Inno Setup でビルドします(docs/BUILD.md)。

出力先は、OneDriveなど同期されるフォルダの外にしてください(同期中のファイルに書き込めず、ビルドが失敗します)。
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";"  # Windows。--add-data の区切り文字


def app_version():
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (ROOT / "app.py").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def find_iscc():
    for c in (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
              str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe")):
        if Path(c).exists():
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HSReviewBuild"))
    ap.add_argument("--installer", action="store_true", help="exeのあと、インストーラー(Inno Setup)も作る")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    icon = ROOT / "assets" / "icon.ico"
    if not icon.exists():
        subprocess.check_call([sys.executable, str(ROOT / "tools" / "make_icon.py")])
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "HSReview", "--icon", str(icon),
        "--distpath", str(out / "dist"), "--workpath", str(out / "build"), "--specpath", str(out),
        "--add-data", f"{ROOT / 'web'}{SEP}web",
        "--add-data", f"{ROOT / 'knowledge'}{SEP}knowledge",
        "--add-data", f"{ROOT / 'docs' / 'DISCLAIMER.md'}{SEP}docs",
        "--add-data", f"{icon}{SEP}assets",
        "--collect-submodules", "hslog",
        "--collect-submodules", "hearthstone",
        "--collect-all", "anthropic",
        "--collect-submodules", "keyring",
        "--collect-all", "certifi",
        "--paths", str(ROOT),
        str(ROOT / "app.py"),
    ]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    exe = out / "dist" / "HSReview" / "HSReview.exe"
    print("\nできました:", exe, "(存在: %s)" % exe.exists())
    if args.installer:
        iscc = find_iscc()
        if not iscc:
            sys.exit("Inno Setup 6 が見つかりません。 winget install JRSoftware.InnoSetup で入れてから、もう一度実行してください。")
        subprocess.check_call([iscc, f"/DSrcDir={out / 'dist' / 'HSReview'}", f"/DAppVersion={app_version()}",
                               f"/O{out / 'installer'}", str(ROOT / "installer" / "HSReview.iss")], cwd=ROOT)
        print("\nインストーラー:", out / "installer" / f"HSReview-Setup-{app_version()}.exe")


if __name__ == "__main__":
    main()
