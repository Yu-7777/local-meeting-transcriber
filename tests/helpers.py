"""テストが自分で用意する入力.

利用者の録音やモデルに依存すると、その PC でしか動かないテストになる。
必要なものは毎回その場で作り、終わったら消す。
"""

import json
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# 実機のループバックと同じ形式。中身は誰も読まないので長さは最小限
RATE, CHANNELS, SECONDS = 48000, 2, 0.1


def write_wav(path):
    """無音の WAV を作る。形式だけ合っていればよい."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<h", 0) * CHANNELS * int(RATE * SECONDS))


def make_recording(base, name="2026_01_02_03_04", both=True):
    """録音フォルダ（meta.json + WAV）を作って、そのパスを返す."""
    folder = Path(base) / name
    streams = {"system": {"file": "system.wav", "label": "相手"}}
    if both:
        streams["mic"] = {"file": "mic.wav", "label": "自分"}

    for info in streams.values():
        write_wav(folder / info["file"])
        info.update(device="テスト用", rate=RATE, channels=CHANNELS,
                    start_delay_sec=0.0, gap_filled_sec=0.0,
                    recorded_sec=0.1, overflows=0)

    (folder / "meta.json").write_text(json.dumps(
        {"started_at": "2026-01-02T03:04:05+09:00",
         "wall_duration_sec": 0.1, "streams": streams},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def make_model_dir(base, repo_id, downloaded=True):
    """HuggingFace のキャッシュ構造を模した空のモデル置き場を作る."""
    snap = (Path(base) / ("models--" + repo_id.replace("/", "--"))
            / "snapshots" / "0123456789abcdef")
    snap.mkdir(parents=True, exist_ok=True)
    if downloaded:
        # 本体があるかどうかで「取得済み」を判定している（download_models 参照）
        (snap / "model.bin").write_bytes(b"dummy")
