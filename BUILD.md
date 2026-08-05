# exe のビルドと配布

## ビルド

```
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

`dist\MeetingTranscriber\` にフォルダ一式ができます。

## 配布物の構成

```
MeetingTranscriber\
├── MeetingTranscriber.exe      ← これを実行
├── _internal\                  ← Python 本体と DLL 群（触らない）
├── models\                     ← 別途コピーが必要（約4.5GB）
└── recordings\                 ← 実行時に自動生成
```

**`models\` は exe に同梱していません。** 4.5GB あり、exe に含めると
ビルドも起動も現実的でなくなるためです。配布時は次のどちらかを選びます。

1. `models\` フォルダを一緒に配る（USB / 社内ファイルサーバ経由）
2. 配布先で初回に取得させる
   ```
   MeetingTranscriber.exe download --all
   ```

## onedir にした理由

`--onefile` にすると起動のたびに 300MB 超の DLL を temp へ展開するため、
起動が数十秒かかり、ネイティブ依存の多い構成では失敗も起きやすくなります。
市販ソフトと同じく「exe + DLL 群のフォルダ」形式にしています。

## サブコマンド

exe 単体で CLI としても動きます（GUI から文字起こしを呼ぶ際も同じ経路）。

```
MeetingTranscriber.exe                      GUI
MeetingTranscriber.exe devices              デバイス一覧
MeetingTranscriber.exe record --seconds 60  録音
MeetingTranscriber.exe transcribe <dir> --diarize
MeetingTranscriber.exe download --all       モデル取得
```

## 配布時の注意: コード署名

**署名なしの exe は Windows に警告されます。**

- SmartScreen: 「WindowsによってPCが保護されました」→ 詳細情報 → 実行
- スマート アプリ コントロールが有効な環境では**ブロックされることがあります**
  （本プロジェクトでも開発中に PyAV の DLL が実際にブロックされました）

社内配布なら「実行」を押してもらう運用で回りますが、広く配るなら
コード署名証明書（年額数万円〜、EV 証明書はさらに高額）が必要です。

なお **Python スクリプトのまま配るほうがブロックされにくい**という逆転があります。
社内の少人数に配るだけなら、ソース（約70KB）+ `setup.bat` のほうが手軽です。

## 前提

- **Windows 専用**です。WASAPI ループバックに依存しているため、
  macOS / Linux では動きません（移植ではなく作り直しになります）。
- 配布先の PC に Python は不要です。
