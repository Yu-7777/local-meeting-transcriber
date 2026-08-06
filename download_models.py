"""音声認識モデルを models/ に事前ダウンロードする.

取得済みのモデルは transcribe 側が local_files_only で読むため、以降は
ネットワークに触れない。音声や文字起こし結果が外部に送信されることは
一切ない。
"""

import argparse
import io
import sys
import tarfile
import urllib.request

import common
import diarization
from apppaths import MODELS_DIR

# モデルの正本。名前を増やす時はここだけ直す
MODELS = {
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1.6),
    "large-v3": ("Systran/faster-whisper-large-v3", 2.9),
}
ALL_MODELS = list(MODELS)
DEFAULT_MODEL = ALL_MODELS[0]

SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMB_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)
# 置き場所は diarization.py が正本
SEG_PATH = diarization.SEG_MODEL
EMB_PATH = diarization.EMB_MODEL
DIA_DIR = SEG_PATH.parent


def model_size(name):
    """モデルのおおよそのサイズ (GB)。未知の名前なら None."""
    entry = MODELS.get(name)
    return entry[1] if entry else None


def is_downloaded(name):
    """モデルが取得済みかを返す。ネットワークには触れない."""
    entry = MODELS.get(name)
    if not entry:
        return False
    snapshots = MODELS_DIR / ("models--" + entry[0].replace("/", "--")) / "snapshots"
    if not snapshots.is_dir():
        return False
    # 途中で失敗した場合フォルダだけ残るので、本体の有無で判定する
    return any(s.joinpath("model.bin").exists() for s in snapshots.iterdir())


def size_note(name):
    """未取得なら「約N GBのダウンロードが要る」旨の文字列、取得済みなら空文字."""
    if is_downloaded(name):
        return ""
    gb = model_size(name)
    return f"未取得（初回に約 {gb} GB のダウンロードが必要）" if gb else "未取得"


def _fetch(url):
    """URL の中身を丸ごと取得して返す."""
    print(f"  {url.split('/')[-1]} を取得中...")
    with urllib.request.urlopen(url) as r:
        return r.read()


def download_diarization():
    """話者分離用の ONNX モデルを取得する（合計 約34MB）."""
    DIA_DIR.mkdir(parents=True, exist_ok=True)

    if SEG_PATH.exists():
        print("  セグメンテーションモデルは取得済み")
    else:
        # 書庫で配られているので、中の model.onnx だけを取り出す
        with tarfile.open(fileobj=io.BytesIO(_fetch(SEG_URL)), mode="r:bz2") as tar:
            member = next(m for m in tar.getmembers() if m.name.endswith("model.onnx"))
            SEG_PATH.write_bytes(tar.extractfile(member).read())

    if EMB_PATH.exists():
        print("  話者埋め込みモデルは取得済み")
    else:
        EMB_PATH.write_bytes(_fetch(EMB_URL))

    print(f"--- 話者分離モデル取得完了 ({DIA_DIR}) ---\n")


def download(name):
    from faster_whisper import WhisperModel

    if is_downloaded(name):
        print(f"--- {name} は取得済み ---\n")
        return

    gb = model_size(name)
    size = f"（約 {gb} GB）" if gb else ""
    print(f"--- {name} を取得しています{size} ---")
    print("    回線によっては時間がかかります。")
    WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODELS_DIR))
    print(f"--- {name} 取得完了 ---\n")


def main():
    ap = argparse.ArgumentParser(description="Whisper モデルを事前取得する")
    ap.add_argument("models", nargs="*", help="取得するモデル名")
    ap.add_argument("--all", action="store_true", help="turbo・large-v3・話者分離モデルをまとめて取得")
    ap.add_argument("--diarization", action="store_true",
                    help="話者分離用モデルも取得する (約34MB)")
    args = ap.parse_args()
    common.use_utf8_stdout()

    want_diarization = args.diarization or args.all
    if args.all:
        targets = ALL_MODELS
    elif args.models:
        targets = args.models
    elif want_diarization:
        targets = []  # --diarization 単独なら話者分離モデルだけ取る
    else:
        targets = [DEFAULT_MODEL]

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name in targets:
        download(name)

    if want_diarization:
        print("--- 話者分離モデルを取得しています ---")
        download_diarization()

    print(f"保存先: {MODELS_DIR}")
    for name in ALL_MODELS:
        print(f"  {name:<18}{size_note(name) or '取得済み'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
