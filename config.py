"""設定 (config.json) の読み書き。読めなければ既定値に戻す."""

import json
import os
from pathlib import Path

from apppaths import RECORDINGS_DIR, ROOT

CONFIG_PATH = ROOT / "config.json"

# 上限 8。P コアを使い切ると E コアを掴んで遅くなるため（実測は README）。
# 測ったのは i7-1260P だけなので、速い CPU では config.json で上げてよい。
DEFAULT_THREADS = min(8, os.cpu_count() or 4)  # or 4: cpu_count は稀に None

DEFAULTS = {
    # 録音 (WAV) の保存先。容量を食うのでドライブごと変えられる
    "recordings_dir": str(RECORDINGS_DIR),
    # 文字起こしの出力先。空文字は「入力と同じ場所」
    "transcripts_dir": "",
    "model": "large-v3-turbo",
    "threads": DEFAULT_THREADS,
    "auto_transcribe": False,
}


def _known(values):
    """DEFAULTS に無いキーは受け付けない（設定ファイルの汚れを持ち込まない）."""
    return {k: v for k, v in values.items() if k in DEFAULTS}


def load():
    cfg = dict(DEFAULTS)
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):  # ValueError は構文エラーと文字コード両方
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
