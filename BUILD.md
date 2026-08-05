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

**このリポジトリは exe を配布していません。** ソース + `setup.bat` を配る形に
しています。理由は以下のとおりです。

### スマート アプリ コントロール (SAC) の挙動

SAC はバイナリを読み込むたびに、次のどちらかを満たすか検査します。

1. Microsoft のクラウド評価が「安全」と判定している
2. Trusted Root Program の CA が発行した証明書で署名されている

**未知かつ署名なしのコードはブロックされ、個別に許可する手段はありません。**

このプロジェクトでビルドした exe は、実測で **8 回連続ブロックされた日**と
**3 回連続で通った日**がありました。署名がないため判定がクラウドの応答次第に
なり、配布先で通るかを予測できません。

### 署名しても解決しません

依存パッケージのネイティブバイナリ 117 個のうち、**署名があるのは 6 個だけ**です
（onnxruntime と NVIDIA 製の一部）。SAC は読み込むバイナリを個別に検査するため、
exe に署名しても、その中から無署名の `av` や `sherpa_onnx` を読んだ時点で
ブロックされます。実際に、PSF 署名済みの python.exe が無署名の `input.pyd` を
読んだところでブロックされました。

完全に解決するには 111 個すべてに署名し直す必要があり、現実的ではありません。

### 代わりにやっていること

`requirements.txt` で**推移的な依存まですべてバージョンを固定**し、SAC が
有効な Windows 11 で動作を確認済みの組み合わせだけを使います。ブロックされる
のは評価の付いていない新しいバージョンなので、これが最も効きます。

なお **SAC が有効なのは Windows 11 をクリーンインストールした環境だけ**で、
開発者と判定されたユーザーでは自動的に無効になります。多数派は影響を受けません。

### 署名する場合

- 証明書は **RSA のみ**（ECC は非対応）。EV である必要はありません
- Trusted Signing (旧 Azure Code Signing) が Microsoft の推奨手段です
- OSS なら SignPath Foundation が無料枠を提供しています

## 前提

- **Windows 専用**です。WASAPI ループバックに依存しているため、
  macOS / Linux では動きません（移植ではなく作り直しになります）。
- 配布先の PC に Python は不要です。
