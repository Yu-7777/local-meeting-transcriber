"""録音した 2 本の WAV をローカルで文字起こしし、時系列にマージする.

system.wav (相手) と mic.wav (自分) を別々に認識するため、話者ダイアライゼーション
なしで「誰が話したか」が確定する。単体の音声ファイルも受け付ける。

使い方は CLI.md。
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import common
import config
from apppaths import MODELS_DIR
from common import AUDIO_SUFFIXES, hhmmss

# VAD が捨てる無音の最短長。ライブラリ既定 2000 からの変更で、実測 4 時間ぶんの
# 録音では 5.3% 多く無音を捨てられた（文字起こし結果はほぼ同一。BUILD.md 参照）。
MIN_SILENCE_MS = 500


def latest_recording(base=None):
    dirs = common.list_recordings(base)
    if not dirs:
        raise SystemExit(
            f"{base or config.recordings_dir()} に録音が見つかりません。"
            "先に録音するか、音声ファイルを直接指定してください。"
        )
    return dirs[0]


def resolve_outdir(explicit, fallback):
    """出力先を決める。明示指定 > 設定値 > 入力と同じ場所 の優先順."""
    if explicit:
        return Path(explicit)
    return config.transcripts_dir() or Path(fallback)


def build_plan(target, outdir=None):
    """入力の指定から (出力ファイル, [(wav, ラベル, 話者分離するか)], meta) を組み立てる.

    出力先だけでなくファイル名まで here で決める。呼び出し側に「外に出す時は
    接頭辞を付ける」を覚えさせると、忘れた時に静かに上書きが起きるため。

    受け付ける形:
      - 録音フォルダ (meta.json あり) -> 相手/自分の 2 本
      - 単体の音声ファイル            -> 1 本のみ（相手/自分は付かない）
      - 省略                          -> 既定の保存先から最新の録音
    """
    target = Path(target) if target else latest_recording()
    if not target.exists():
        raise SystemExit(f"指定されたパスが見つかりません: {target}")

    if target.is_file():
        if target.suffix.lower() not in AUDIO_SUFFIXES:
            raise SystemExit(f"対応していない形式です: {target.suffix}")
        out = resolve_outdir(outdir, target.parent)
        out.mkdir(parents=True, exist_ok=True)
        # 単体ファイルは誰の声か分からないので 相手/自分 を付けない。
        # --diarize を付けた場合は声質で分けた 話者N が付く
        return (out / f"{target.stem}_transcript.txt",
                [(target, None, True)], {"single_file": True})

    meta_path = target / "meta.json"
    if not meta_path.exists():
        raise SystemExit(
            f"{target} は録音フォルダではありません（meta.json がありません）。\n"
            "音声ファイルを直接指定することもできます。"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    items = []
    for name in ("system", "mic"):
        info = meta["streams"].get(name)
        if not info:
            continue
        wav = target / info["file"]
        if not wav.exists():
            print(f"  ※ {wav.name} が見つかりません。スキップします。")
            continue
        items.append((wav, info["label"], name == "system"))

    out = resolve_outdir(outdir, target)
    out.mkdir(parents=True, exist_ok=True)
    # 録音フォルダの中に出すなら transcript.txt のままでよい。外に出す場合は
    # 録音ごとに同名になって上書きしてしまうので、録音フォルダ名を前に付ける。
    same_place = out.resolve() == target.resolve()
    name = "transcript.txt" if same_place else f"{target.name}_transcript.txt"
    return out / name, items, meta


def run_diarization(wav_path, num_speakers, threads, threshold):
    """相手チャンネルを話者ごとに分ける。失敗しても文字起こしは続行する."""
    import diarization

    print("\n--- 話者分離 (相手チャンネル) ---")
    if num_speakers:
        print(f"  人数を {num_speakers} 人として処理します")
    else:
        print(f"  人数は自動推定します (threshold={threshold})")
        print("  ※ 人数が合わない時は --diar-threshold を上下（大きいほど人数減）")

    def progress(processed, total):
        print(f"\r  {processed / total * 100:5.1f}%   ", end="", flush=True)
        return 0

    # SystemExit は BaseException 直下なので、下の except Exception では
    # 捕まらずそのまま呼び出し元へ抜ける（モデル未取得の案内を潰さないため）
    try:
        turns, stats = diarization.diarize(
            wav_path, num_speakers=num_speakers, threshold=threshold,
            threads=threads, on_progress=progress
        )
    except Exception as exc:
        print(f"\n  ※ 話者分離に失敗しました: {exc}")
        print("  ※ 話者ラベルなしで続行します。")
        return []

    print(f"\r  100.0%  完了  ({stats['kept']} 名を検出 / {len(turns)} 区間)      ")
    if stats["dropped"]:
        print(f"  発話が {diarization.MIN_SPEAKER_SEC:.0f} 秒未満のクラスタ "
              f"{stats['dropped']} 件を除外（BGM・効果音・音質変化による誤検出）")
    return turns


def transcribe_stream(model, wav_path, label, language, beam_size):
    """1 本の WAV を認識してセグメントのリストを返す."""
    print(f"\n--- {label} ({wav_path.name}) ---")
    # 会話は片チャンネルの半分以上が無音になる。VAD で無音を落とすと速くなり、
    # 同時に無音への幻聴（「ご視聴ありがとうございました」等）も減る。
    # condition_on_previous_text は既定 True からの変更。直前の文を条件に
    # 使うと、一度幻聴が出たあと同じ文を延々と繰り返すため切っている。
    segments, info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=MIN_SILENCE_MS),
        condition_on_previous_text=False,
    )
    total = info.duration or 0.0
    print(f"  長さ {hhmmss(total)} / 判定言語 {info.language} "
          f"(確信度 {info.language_probability:.2f})")

    results = []
    prev_text = None
    started = time.perf_counter()
    for seg in segments:
        text = seg.text.strip()
        pct = min(100.0, seg.end / total * 100) if total else 0.0
        print(f"\r  {pct:5.1f}%  {hhmmss(seg.end)}   ", end="", flush=True)

        if not text:
            continue
        if text == prev_text:  # 同じ行の繰り返しは幻聴の典型
            continue
        prev_text = text

        results.append({
            "speaker": label,
            "start": seg.start,
            "end": seg.end,  # 話者分離の区間との重なり判定に使う
            "text": text,
        })

    elapsed = time.perf_counter() - started
    speed = total / elapsed if elapsed > 0 else 0.0
    print(f"\r  100.0%  完了  ({hhmmss(elapsed)} で処理 / 実時間比 {speed:.2f}x, "
          f"{len(results)} セグメント)      ")
    return results


def announce(target, items, out_path, model_name, threads):
    """何をどこに出すのかを先に見せる（数十分かかるので確認できるように）."""
    print("=" * 68)
    print(f"  対象   : {target or '(最新の録音)'}")
    for wav, label, _ in items:
        print(f"           {wav.name}" + (f"  [{label}]" if label else ""))
    print(f"  出力   : {out_path}")
    print(f"  モデル : {model_name} (CPU / int8 / {threads} threads)")
    print("=" * 68)


def load_model(model_name, threads, offline):
    """モデルを読み込む.

    取得済みなら必ずローカルだけを見る（毎回 Hub へ更新確認に行かせない）。
    """
    import download_models

    have_model = download_models.is_downloaded(model_name)
    if not have_model:
        if offline:
            raise SystemExit(
                f"\n{model_name} はまだ取得していません"
                f"（{download_models.size_text(model_name)}）。\n"
                "--offline を外して実行するか、先に次を実行してください:\n"
                f"  {common.cli_hint('download', model_name)}"
            )
        print(f"\n※ {model_name}: {download_models.download_notice(model_name)}")

    from faster_whisper import WhisperModel

    print("\nモデルを読み込んでいます...")
    started = time.perf_counter()
    model = WhisperModel(
        model_name,
        device="cpu",
        # float32 の 2.2 倍速く、出力は約 95% 一致（実測は BUILD.md）
        compute_type="int8",
        cpu_threads=threads,
        download_root=str(MODELS_DIR),
        local_files_only=have_model or offline,
    )
    print(f"読み込み完了 ({time.perf_counter() - started:.1f}s)")
    return model


def format_transcript(segments, meta, source_name, model_name):
    """本文を組み立てて行のリストで返す（ファイルには触れない）."""
    lines = ["# 会議文字起こし"]
    if meta.get("single_file"):
        lines.append(f"# 元ファイル : {source_name}")
    else:
        lines.append(f"# 録音日時 : {meta.get('started_at', '?')}")
        lines.append(f"# 録音長   : {hhmmss(meta.get('wall_duration_sec', 0))}")
    lines += [f"# モデル   : {model_name}", ""]

    for s in segments:
        speaker = s["speaker"]
        lines.append(f"[{hhmmss(s['start'])}] "
                     + (f"{speaker}: " if speaker else "") + s["text"])
    return lines


def main():
    ap = argparse.ArgumentParser(description="録音をローカルで文字起こしする")
    ap.add_argument("recording", nargs="?", type=Path, default=None,
                    help="録音フォルダ、または音声ファイル（省略時は最新の録音）")
    ap.add_argument("--outdir", type=Path, default=None,
                    help="文字起こし結果の出力先（省略時は設定値）")
    ap.add_argument("--model", default=None,
                    help="モデル名 (large-v3-turbo / large-v3 など)")
    ap.add_argument("--language", default="ja", help="言語コード。auto で自動判定")
    ap.add_argument("--threads", type=int, default=None, help="CPU スレッド数")
    ap.add_argument("--beam-size", type=int, default=5, help="ビームサイズ")
    ap.add_argument("--offline", action="store_true",
                    help="ダウンロード済みモデルのみ使う（ネットワークに触れない）")
    ap.add_argument("--diarize", action="store_true",
                    help="相手を声質で話者ごとに分ける（相手が複数人の時）")
    ap.add_argument("--speakers", type=int, default=None,
                    help="相手側の人数。まず自動を試すこと"
                         "（実測では指定しないほうが良い結果だった）")
    ap.add_argument("--diar-threshold", type=float, default=None,
                    help="話者分離の統合しやすさ。大きいほど人数が減る")
    args = ap.parse_args()
    common.use_utf8_stdout()

    cfg = config.load()
    model_name = args.model or cfg["model"]
    threads = args.threads or cfg["threads"]

    transcript_path, items, meta = build_plan(args.recording, args.outdir)
    announce(args.recording, items, transcript_path, model_name, threads)

    model = load_model(model_name, threads, args.offline)

    language = None if args.language == "auto" else args.language

    all_segments = []
    for wav_path, label, diarizable in items:
        segs = transcribe_stream(
            model, wav_path, label or "", language, args.beam_size
        )

        # 相手チャンネルだけ話者分離する（自分は分ける必要がない）
        if args.diarize and diarizable:
            import diarization

            thr = args.diar_threshold or diarization.DEFAULT_THRESHOLD
            turns = run_diarization(wav_path, args.speakers, threads, thr)
            spoke = diarization.label_segments(segs, turns, label)
            if spoke:
                print(f"  実際に発言した話者: {spoke} 名")

        all_segments.extend(segs)

    all_segments.sort(key=lambda s: s["start"])

    lines = format_transcript(all_segments, meta, items[0][0].name, model_name)
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_speaker = Counter(s["speaker"] or "(話者なし)" for s in all_segments)

    print()
    print("=" * 68)
    print(f"  {transcript_path}")
    print(f"  合計 {len(all_segments)} セグメント "
          + " / ".join(f"{k} {v}" for k, v in by_speaker.items()))
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
