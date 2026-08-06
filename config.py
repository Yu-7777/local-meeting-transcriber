"""ユーザー設定の読み書き（保存先ディレクトリなど）.

設定は exe / スクリプトの隣の config.json に置く。
壊れていても落ちないよう、読めなければ既定値に戻す。
"""

import json
import os
from pathlib import Path

from apppaths import RECORDINGS_DIR, ROOT

CONFIG_PATH = ROOT / "config.json"

# 文字起こしのスレッド数。実測（i7-1260P / large-v3-turbo / int8）では
#   4 threads 37.0s / 8 threads 24.8s / 12 threads 26.9s
# と 8 前後で頭打ちになり、それ以上は E コアを掴んで逆に遅くなる。
DEFAULT_THREADS = min(8, os.cpu_count() or 4)

DEFAULTS = {
    # 録音 (WAV) の保存先。1 時間で約 1.3GB 使うため容量のあるドライブを選べる
    "recordings_dir": str(RECORDINGS_DIR),
    # 文字起こしの出力先。空文字は「入力と同じ場所」の意味。
    # 議事録だけを別の場所（同期フォルダ等）にまとめたい場合に設定する
    "transcripts_dir": "",
    "model": "large-v3-turbo",
    "threads": DEFAULT_THREADS,
    # 録音を停止したら、そのまま文字起こしまで走らせるか
    "auto_transcribe": False,
}


def _known(values):
    """DEFAULTS に無いキーは受け付けない（設定ファイルの汚れを持ち込まない）."""
    return {k: v for k, v in values.items() if k in DEFAULTS}


def load():
    cfg = dict(DEFAULTS)
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return cfg  # 無い・壊れている -> 既定値で続行する
    if isinstance(saved, dict):
        cfg.update(_known(saved))
    return cfg


def save(**changes):
    cfg = load()
    cfg.update(_known(changes))
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return cfg


def recordings_dir():
    return Path(load()["recordings_dir"])


def transcripts_dir():
    """設定されていれば Path、未設定なら None（＝入力と同じ場所に出す）."""
    value = str(load()["transcripts_dir"]).strip()
    return Path(value) if value else None
