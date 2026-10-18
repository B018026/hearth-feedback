# Windows版(exe・インストーラー)のビルド手順

Pythonが入っていないPCでも動く、Windows用のアプリを作る手順です。

## 仕組み

- **PyInstaller** で、Pythonと必要なライブラリを丸ごと含んだフォルダ(`HSReview.exe`)を作ります。
- **Inno Setup** で、そのフォルダを、インストーラー(`HSReview-Setup-x.y.z.exe`)にまとめます。
- 画面は、Edge(なければChrome)の**アプリモード**で開きます。アドレスバーのない専用ウィンドウで、タイトルバーにアプリのアイコンと名前が出ます。EdgeもChromeも無い場合は、通常のブラウザで開きます。
- exe版は、小さな操作ウィンドウ(「画面を開く」「終了」)も出します。この操作ウィンドウを閉じると、アプリが終了します。二重に起動しても、画面が開くだけです。

## 準備(初回のみ)

```
python -m pip install -r requirements.txt pyinstaller pillow
winget install JRSoftware.InnoSetup
```

## ビルド

```
python build.py               # exeだけ
python build.py --installer   # exe + インストーラー
```

出力先は `%LOCALAPPDATA%\HSReviewBuild\` です(`--out` で変更できます)。

- `dist\HSReview\HSReview.exe` … Pythonなしで動くフォルダ(約50MB。そのまま配布もできます)
- `installer\HSReview-Setup-x.y.z.exe` … インストーラー(約21MB)

**出力先は、OneDriveなど同期されるフォルダの外にしてください。** 同期中のファイルに書き込めず、ビルドが失敗します。

## バージョンを上げるとき

`app.py` の `VERSION` を変えると、インストーラーのファイル名にも反映されます。

免責の文面(`docs/DISCLAIMER.md`)を変えたら、`app.py` の `CONSENT_VERSION` の日付を更新してください。利用者に、もう一度同意を求めます。

## データの保存場所(exe版)

| 内容 | 場所 |
|---|---|
| 試合データ・カードデータ・設定 | `%APPDATA%\HSReview\` |
| APIキー | Windowsの資格情報マネージャー(名前: `HSReview`) |
| エラーの記録 | `%APPDATA%\HSReview\app.log` |
| 専用ウィンドウの設定(Edge/Chromeのプロフィール) | `%APPDATA%\HSReview\browser\` (普段のブラウザには触れません) |

アンインストール時に、試合データを残すか消すかを選べます(初期値は「残す」)。APIキーは自動では消えないので、不要なら資格情報マネージャーから削除してください。

## 配布するときの注意

- **コード署名をしていないため、初回の実行時にWindowsのSmartScreenが警告します**(「WindowsによってPCが保護されました」)。「詳細情報」→「実行」で起動できます。署名には有料の証明書が必要です。
- PyInstallerで作ったexeは、ウイルス対策ソフトに誤検知されることがあります。
- 配布する前に、[注意事項・免責](DISCLAIMER.md)の内容と、Blizzardの方針(特に、有料にする場合)を確認してください。

## 動作確認のしかた(テスト用の環境変数)

本番のデータやAPIキーに触れずに試すときは、次を設定します。

| 環境変数 | 意味 |
|---|---|
| `HSREVIEW_DATA` | データの保存先を別の場所にする |
| `HSREVIEW_KEYRING_SERVICE` | 資格情報マネージャーの名前を別にする(例: `HSReviewTest`) |
| `HSREVIEW_NO_BROWSER=1` | 画面を自動で開かない |
| `HSREVIEW_NORMAL_BROWSER=1` | 専用ウィンドウではなく、通常のブラウザで開く |
| `HSREVIEW_BROWSER_PROFILE` | 専用ウィンドウのプロフィールの場所を変える |
| `HSREVIEW_HEADLESS=1` | exe版でも、操作ウィンドウを出さない |
| `HSREVIEW_FAKE_AI=1` | AIを呼ばず、ダミーの回答を返す(課金なし) |
