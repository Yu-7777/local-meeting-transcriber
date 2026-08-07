"""相手チャンネル(system.wav)を声質で話者ごとに分ける.

sherpa-onnx (ONNX Runtime) を使うため PyTorch もアクセストークンも不要。
自分の声は物理的に別ファイル(mic.wav)なので、ここでは相手側だけを扱えばよく、
一般的な話者分離より条件が良い。

精度の限界と num_speakers の使いどころは CLI.md「精度について正直な注意」。
"""

from collections import defaultdict

import common
import config
from apppaths import MODELS_DIR

SEG_MODEL = MODELS_DIR / "diarization" / "segmentation.onnx"
EMB_MODEL = MODELS_DIR / "diarization" / "embedding.onnx"

# 音質差で同一人物の埋め込みが散るため、低い閾値だと同じ人が複数に割れる。
# 統合寄りにしてある（掃引結果は CLI.md「チューニングの実測データ」）。
DEFAULT_THRESHOLD = 0.9

# BGM や音質変化が「話者」に化けて人数が膨らむのを防ぐ足切り。
# 実会議 11 分では生クラスタ 11 個のうち 6 個が 2.5 秒未満で、本物の話者は
# 5.3 秒以上だった。その谷に置いている（実測は BUILD.md）。
MIN_SPEAKER_SEC = 3.0


def models_available():
    return SEG_MODEL.exists() and EMB_MODEL.exists()


def diarize(wav_path, num_speakers=None, threshold=DEFAULT_THRESHOLD, threads=None,
            on_progress=None):
    """話者分離を行い (turns, stats) を返す.

    turns は (start, end, speaker_index) のリスト（開始時刻順）。
    """
    import sherpa_onnx
    from faster_whisper.audio import decode_audio

    threads = threads or config.load()["threads"]

    if not models_available():
        raise SystemExit(
            "話者分離モデルがありません。次を実行してください:\n"
            f"  {common.cli_hint('download', '--diarization')}"
        )

    sd_config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
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
            # -1 で自動（threshold が人数を決める）
            num_clusters=int(num_speakers) if num_speakers else -1,
            threshold=float(threshold),
        ),
        # sherpa-onnx の既定値のまま（調整していない）
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not sd_config.validate():
        raise SystemExit("話者分離の設定が不正です。モデルファイルを確認してください。")

    sd = sherpa_onnx.OfflineSpeakerDiarization(sd_config)
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
    """文字起こしセグメントに、最も重なりの大きい話者を割り当てる.

    重なりの下限は設けない。Whisper は無音をまたいで区間の終端を伸ばすので
    （実会議で最長 65.9 秒）、区間長に対する割合で足切りすると、時刻が
    伸びただけの正しい区間からラベルを剥がしてしまう。
    """
    best_speaker, best_overlap = None, 0.0
    for t_start, t_end, speaker in turns:
        overlap = min(end, t_end) - max(start, t_start)
        if overlap > best_overlap:
            best_overlap, best_speaker = overlap, speaker
    return best_speaker


def label_segments(segments, turns, base_label):
    """話者ラベルを書き込み、実際に発言した話者数を返す.

    間引きでクラスタ番号が飛ぶので、登場順に 1 から振り直す。
    """
    assigned = [assign_speaker(s["start"], s["end"], turns) for s in segments]
    order = {spk: i for i, spk in
             enumerate(dict.fromkeys(s for s in assigned if s is not None), 1)}
    for seg, spk in zip(segments, assigned):
        if spk is not None:
            seg["speaker"] = (f"{base_label}(話者{order[spk]})" if base_label
                              else f"話者{order[spk]}")
    return len(order)
