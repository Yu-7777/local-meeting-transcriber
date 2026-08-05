"""ユーザー設定の読み書き（保存先ディレクトリなど）.

設定は exe / スクリプトの隣の config.json に置く。
壊れていても落ちないよう、読めなければ既定値に戻す。
"""

import json
from pathlib import Path

from apppaths import RECORDINGS_DIR, ROOT

CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    # 録音 (WAV) の保存先。1 時間で約 1.3GB 使うため容量のあるドライブを選べる
    "recordings_dir": str(RECORDINGS_DIR),
    # 文字起こしの出力先。空文字は「入力と同じ場所」の意味。
    # 議事録だけを別の場所（同期フォルダ等）にまとめたい場合に設定する
    "transcripts_dir": "",
    "model": "large-v3-turbo",
    "threads": 4,
}


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass  # 壊れていたら既定値で続行する
    return cfg


def save(**changes):
    cfg = load()
    cfg.update({k: v for k, v in changes.items() if k in DEFAULTS})
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
