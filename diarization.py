"""相手チャンネル(system.wav)を声質で話者ごとに分ける.

sherpa-onnx (ONNX Runtime) を使うため PyTorch もアクセストークンも不要。
自分の声は物理的に別ファイル(mic.wav)なので、ここでは相手側だけを扱えばよく、
一般的な話者分離より条件が良い。

会議音声はコーデック圧縮とノイズ抑制で劣化しているため、取り違えは起こり得る。
num_speakers を指定すると人数を固定できるが、実測では指定しないほうが
良い結果だった（README の掃引結果を参照）。自動が外れた時だけ使う。
"""

from collections import defaultdict

import common
from apppaths import MODELS_DIR

SEG_MODEL = MODELS_DIR / "diarization" / "segmentation.onnx"
EMB_MODEL = MODELS_DIR / "diarization" / "embedding.onnx"

# 実データ（YouTube の 3 人の対談 5 分）で閾値を掃引した結果:
#   0.5 -> 25 人 / 0.7 -> 14 人 / 0.8 -> 10 人 / 0.9 -> 7 人(実質 3 人)
# 会議音声はコーデック圧縮や場面ごとの音質差で同一人物の埋め込みが散るため、
# 既定の 0.5 では同じ人が複数に割れる。統合寄りの 0.9 を既定にする。
DEFAULT_THRESHOLD = 0.9

# 合計発話がこの秒数に満たないクラスタは、BGM・効果音・音質変化による
# 誤検出とみなして捨てる。これをしないと話者数が実態より大幅に膨らむ。
MIN_SPEAKER_SEC = 3.0


def models_available():
    return SEG_MODEL.exists() and EMB_MODEL.exists()


def diarize(wav_path, num_speakers=None, threshold=DEFAULT_THRESHOLD, threads=8,
            on_progress=None):
    """話者分離を行い (turns, stats) を返す.

    turns は (start, end, speaker_index) のリスト（開始時刻順）。
    """
    import sherpa_onnx
    from faster_whisper.audio import decode_audio

    if not models_available():
        raise SystemExit(
            "話者分離モデルがありません。次を実行してください:\n"
            f"  {common.cli_hint('download', '--diarization')}"
        )

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(SEG_MODEL)
            ),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(EMB_MODEL), num_threads=threads
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            # 人数が既知なら固定する。不明なら -1 で threshold により自動決定。
            num_clusters=int(num_speakers) if num_speakers else -1,
            threshold=float(threshold),
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise SystemExit("話者分離の設定が不正です。モデルファイルを確認してください。")

    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    audio = decode_audio(str(wav_path), sampling_rate=sd.sample_rate)
    result = sd.process(audio, callback=on_progress)
    del audio  # 1 時間で 230MB。Whisper のモデルと同時に抱えないよう早めに解放

    turns = [(s.start, s.end, s.speaker) for s in result.sort_by_start_time()]

    spoken = defaultdict(float)
    for start, end, spk in turns:
        spoken[spk] += end - start

    # 人数を明示指定された場合は、指定どおりに残す（間引くと意図に反するため）
    if num_speakers:
        keep = set(spoken)
    else:
        keep = {spk for spk, sec in spoken.items() if sec >= MIN_SPEAKER_SEC}
        if not keep and spoken:  # 全部短い場合は最長のものだけ残す
            keep = {max(spoken, key=spoken.get)}

    return ([t for t in turns if t[2] in keep],
            {"raw": len(spoken), "kept": len(keep), "dropped": len(spoken) - len(keep)})


def assign_speaker(start, end, turns):
    """文字起こしセグメントに、最も重なりの大きい話者を割り当てる."""
    best_speaker, best_overlap = None, 0.0
    for t_start, t_end, speaker in turns:
        overlap = min(end, t_end) - max(start, t_start)
        if overlap > best_overlap:
            best_overlap, best_speaker = overlap, speaker
    return best_speaker


def label_segments(segments, turns, base_label):
    """文字起こしセグメントに話者ラベルを書き込み、実際に発言した話者数を返す.

    diarize() が短いクラスタを間引くため番号は飛び番になる。登場順に
    1 から振り直して、利用者に見せる番号を詰める。
    ここに置いてあるのは、番号の振り直しが話者分離側の事情だから。
    """
    assigned = [assign_speaker(s["start"], s["end"], turns) for s in segments]
    order = {spk: i for i, spk in
             enumerate(dict.fromkeys(s for s in assigned if s is not None), 1)}
    for seg, spk in zip(segments, assigned):
        if spk is not None:
            seg["speaker"] = (f"{base_label}(話者{order[spk]})" if base_label
                              else f"話者{order[spk]}")
    return len(order)
