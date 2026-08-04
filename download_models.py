"""音声認識モデルを models/ に事前ダウンロードする.

一度取得すれば以降は完全オフラインで動作する（HF_HUB_OFFLINE=1）。
音声や文字起こし結果が外部に送信されることは一切ない。
"""

import argparse
import sys

from apppaths import MODELS_DIR

DEFAULT_MODEL = "large-v3-turbo"
ALL_MODELS = ["large-v3-turbo", "large-v3"]

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

    print(f"--- {name} を取得しています ---")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
