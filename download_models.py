"""音声認識モデルを models/ に事前ダウンロードする.

一度取得すれば以降は完全オフラインで動作する（HF_HUB_OFFLINE=1）。
音声や文字起こし結果が外部に送信されることは一切ない。
"""

import argparse
import sys

from apppaths import MODELS_DIR

DEFAULT_MODEL = "large-v3-turbo"
ALL_MODELS = ["large-v3-turbo", "large-v3"]

# 取得済みかを調べるために、faster-whisper が使う HuggingFace 上の場所を持つ。
# キャッシュは models/models--<org>--<repo>/snapshots/<hash>/model.bin に入る。
MODEL_REPOS = {
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}
MODEL_SIZES = {"large-v3-turbo": 1.6, "large-v3": 2.9}  # GB (目安)

DIA_DIR = MODELS_DIR / "diarization"
SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMB_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
SEG_PATH = DIA_DIR / "segmentation.onnx"
EMB_PATH = DIA_DIR / "embedding.onnx"


def is_downloaded(name):
    """モデルが取得済みかを返す。ネットワークには触れない."""
    repo = MODEL_REPOS.get(name)
    if not repo:
        return False
    snapshots = MODELS_DIR / ("models--" + repo.replace("/", "--")) / "snapshots"
    if not snapshots.is_dir():
        return False
    # 途中で失敗した場合フォルダだけ残るので、本体の有無で判定する
    return any(s.joinpath("model.bin").exists() for s in snapshots.iterdir())


def size_note(name):
    """未取得なら「約N GBのダウンロードが要る」旨の文字列、取得済みなら空文字."""
    if is_downloaded(name):
        return ""
    gb = MODEL_SIZES.get(name)
    return f"未取得（初回に約 {gb} GB のダウンロードが必要）" if gb else "未取得"


def _fetch(url, dest):
    import urllib.request

    print(f"  {url.split('/')[-1]} を取得中...")
    with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
        f.write(r.read())


def download_diarization():
    """話者分離用の ONNX モデルを取得する（合計 約34MB）."""
    import io
    import tarfile

    DIA_DIR.mkdir(parents=True, exist_ok=True)

    if SEG_PATH.exists():
        print("  セグメンテーションモデルは取得済み")
    else:
        import urllib.request

        print(f"  {SEG_URL.split('/')[-1]} を取得中...")
        with urllib.request.urlopen(SEG_URL) as r:
            blob = r.read()
        # 書庫の中の model.onnx だけを取り出す
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:bz2") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("model.onnx"))
            src = tar.extractfile(member)
            SEG_PATH.write_bytes(src.read())

    if EMB_PATH.exists():
        print("  話者埋め込みモデルは取得済み")
    else:
        _fetch(EMB_URL, EMB_PATH)

    print(f"--- 話者分離モデル取得完了 ({DIA_DIR}) ---\n")


def download(name):
    from faster_whisper import WhisperModel

    if is_downloaded(name):
        print(f"--- {name} は取得済み ---\n")
        return

    gb = MODEL_SIZES.get(name)
    size = f"（約 {gb} GB）" if gb else ""
    print(f"--- {name} を取得しています{size} ---")
    print("    回線によっては時間がかかります。")
    WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODELS_DIR))
    print(f"--- {name} 取得完了 ---\n")


def main():
    ap = argparse.ArgumentParser(description="Whisper モデルを事前取得する")
    ap.add_argument("models", nargs="*", default=None, help="取得するモデル名")
    ap.add_argument("--all", action="store_true", help="turbo と large-v3 の両方を取得")
    ap.add_argument("--diarization", action="store_true",
                    help="話者分離用モデルも取得する (約34MB)")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.all:
        targets = ALL_MODELS
    elif args.models:
        targets = args.models
    elif args.diarization:
        targets = []  # --diarization 単独なら話者分離モデルだけ取る
    else:
        targets = [DEFAULT_MODEL]

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name in targets:
        download(name)

    if args.diarization or args.all:
        print("--- 話者分離モデルを取得しています ---")
        download_diarization()

    print(f"保存先: {MODELS_DIR}")
    for name in ALL_MODELS:
        note = size_note(name)
        print(f"  {name:<18}{note or '取得済み'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
