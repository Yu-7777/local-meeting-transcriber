"""録音した 2 本の WAV をローカルで文字起こしし、時系列にマージする.

system.wav (相手) と mic.wav (自分) を別々に認識するため、話者ダイアライゼーション
なしで「誰が話したか」が確定する。すべてローカルで完結し、外部送信は行わない。

使い方:
    python transcribe.py                      # recordings/ の最新を処理
    python transcribe.py recordings/20260802_164924
    python transcribe.py --model large-v3     # 精度優先（時間は数倍かかる）
"""

import argparse
import json
import sys
import time
from pathlib import Path

import config
from apppaths import MODELS_DIR

# 無音区間に対する幻聴を落とすための閾値
NO_SPEECH_THRESHOLD = 0.8

# 単体ファイルを直接指定された場合に受け付ける拡張子
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wma",
                  ".mp4", ".mkv", ".webm", ".mov"}


def hhmmss(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def latest_recording(base=None):
    base = Path(base) if base else config.recordings_dir()
    if not base.exists():
        raise SystemExit(
            f"{base} がありません。先に録音するか、対象を引数で指定してください。"
        )
    dirs = [d for d in base.iterdir() if d.is_dir() and (d / "meta.json").exists()]
    if not dirs:
        raise SystemExit(
            f"{base} に録音が見つかりません。"
            "先に録音するか、音声ファイルを直接指定してください。"
        )
    return max(dirs, key=lambda d: d.stat().st_mtime)


def resolve_outdir(explicit, fallback):
    """出力先を決める。明示指定 > 設定値 > 入力と同じ場所 の優先順."""
    if explicit:
        return Path(explicit)
    configured = config.transcripts_dir()
    return configured if configured else Path(fallback)


def build_plan(target, outdir=None):
    """入力の指定から (出力先, [(wav, ラベル, 話者分離するか)], meta) を組み立てる.

    受け付ける形:
      - 録音フォルダ (meta.json あり) -> 相手/自分の 2 本
      - 単体の音声ファイル            -> 1 本のみ（話者ラベルなし）
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
        meta = {"started_at": "-", "wall_duration_sec": 0, "single_file": True}
        # 単体ファイルは誰の声か分からないので話者ラベルを付けない
        return out, [(target, None, True, 0.0)], meta, target.stem

    meta_path = target / "meta.json"
    if not meta_path.exists():
        raise SystemExit(
            f"{target} は録音フォルダではありません（meta.json がありません）。\n"
            "音声ファイルを直接指定することもできます。"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    aligned = meta.get("aligned", False)
    items = []
    for name in ("system", "mic"):
        info = meta["streams"].get(name)
        if not info:
            continue
        wav = target / info["file"]
        if not wav.exists():
            print(f"  ※ {wav.name} が見つかりません。スキップします。")
            continue
        offset = 0.0 if aligned else float(info.get("start_delay_sec", 0.0))
        items.append((wav, info["label"], name == "system", offset))

    out = resolve_outdir(outdir, target)
    out.mkdir(parents=True, exist_ok=True)
    # 録音フォルダの中に出すなら transcript.txt のままでよい。外に出す場合は
    # 録音ごとに同名になって上書きしてしまうので、録音フォルダ名を前に付ける。
    same_place = out.resolve() == target.resolve()
    return out, items, meta, None if same_place else target.name


def assign_speaker_safe(turns, start, end):
    if not turns:
        return None
    import diarization

    return diarization.assign_speaker(start, end, turns)


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

    try:
        turns, stats = diarization.diarize(
            wav_path, num_speakers=num_speakers, threshold=threshold,
            threads=threads, on_progress=progress
        )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  ※ 話者分離に失敗しました: {exc}")
        print("  ※ 話者ラベルなしで続行します。")
        return []

    print(f"\r  100.0%  完了  ({stats['kept']} 名を検出 / {len(turns)} 区間)      ")
    if stats["dropped"]:
        print(f"  発話が {diarization.MIN_SPEAKER_SEC:.0f} 秒未満のクラスタ "
              f"{stats['dropped']} 件を除外（BGM・効果音・音質変化による誤検出）")
    return turns


def transcribe_stream(model, wav_path, label, offset, language, beam_size):
    """1 本の WAV を認識してセグメントのリストを返す."""
    print(f"\n--- {label} ({wav_path.name}) ---")
    segments, info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
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
        if seg.no_speech_prob > NO_SPEECH_THRESHOLD:
            continue
        if text == prev_text:  # 同じ行の繰り返しは幻聴の典型
            continue
        prev_text = text

        results.append({
            "speaker": label,
            "start": seg.start + offset,
            "end": seg.end + offset,  # 話者分離の区間との重なり判定に使う
            "text": text,
        })

    elapsed = time.perf_counter() - started
    speed = total / elapsed if elapsed > 0 else 0.0
    print(f"\r  100.0%  完了  ({hhmmss(elapsed)} で処理 / 実時間比 {speed:.2f}x, "
          f"{len(results)} セグメント)      ")
    return results


def main():
    ap = argparse.ArgumentParser(description="録音をローカルで文字起こしする")
    ap.add_argument("recording", nargs="?", type=Path, default=None,
                    help="録音フォルダ、または音声ファイル（省略時は最新の録音）")
    ap.add_argument("--outdir", type=Path, default=None,
                    help="文字起こし結果の出力先"
                         "（省略時は設定値、未設定なら入力と同じ場所）")
    ap.add_argument("--model", default=None,
                    help="モデル名 (large-v3-turbo / large-v3 など)")
    ap.add_argument("--language", default="ja", help="言語コード。auto で自動判定")
    ap.add_argument("--threads", type=int, default=None, help="CPU スレッド数")
    ap.add_argument("--beam-size", type=int, default=5, help="ビームサイズ")
    ap.add_argument("--offline", action="store_true",
                    help="ダウンロード済みモデルのみ使う（ネットワークに触れない）")
    ap.add_argument("--diarize", action="store_true",
                    help="相手チャンネルを声質で話者ごとに分ける（相手が3人以上の時）")
    ap.add_argument("--speakers", type=int, default=None,
                    help="相手側の人数。分かっていれば指定すると精度が上がる")
    ap.add_argument("--diar-threshold", type=float, default=None,
                    help="話者分離の統合しやすさ。大きいほど人数が減る (既定 0.9)")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = config.load()
    model_name = args.model or cfg["model"]
    threads = args.threads or cfg["threads"]

    out_dir, items, meta, stem = build_plan(args.recording, args.outdir)

    print("=" * 68)
    print(f"  対象   : {args.recording or '(最新の録音)'}")
    for wav, label, _, _ in items:
        print(f"           {wav.name}" + (f"  [{label}]" if label else ""))
    print(f"  出力   : {out_dir}")
    print(f"  モデル : {model_name} (CPU / int8 / {threads} threads)")
    print("=" * 68)

    from faster_whisper import WhisperModel

    print("\nモデルを読み込んでいます...")
    load_start = time.perf_counter()
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        download_root=str(MODELS_DIR),
        local_files_only=args.offline,
    )
    print(f"読み込み完了 ({time.perf_counter() - load_start:.1f}s)")

    language = None if args.language == "auto" else args.language

    all_segments = []
    for wav_path, label, diarizable, offset in items:
        segs = transcribe_stream(
            model, wav_path, label or "", offset, language, args.beam_size
        )

        # 相手チャンネルだけ話者分離する（自分は分ける必要がない）
        if args.diarize and diarizable:
            import diarization as _dia
            thr = args.diar_threshold or _dia.DEFAULT_THRESHOLD
            turns = run_diarization(wav_path, args.speakers, threads, thr)
            raw = [
                assign_speaker_safe(turns, s["start"] - offset, s["end"] - offset)
                for s in segs
            ]
            # クラスタ番号は飛び番になることがあるので、登場順に振り直す
            order = {}
            for spk in raw:
                if spk is not None and spk not in order:
                    order[spk] = len(order) + 1
            for s, spk in zip(segs, raw):
                if spk is not None:
                    s["speaker"] = (f"{label}(話者{order[spk]})" if label
                                    else f"話者{order[spk]}")
            if order:
                print(f"  実際に発言した話者: {len(order)} 名")

        all_segments.extend(segs)

    all_segments.sort(key=lambda s: s["start"])

    prefix = f"{stem}_" if stem else ""
    lines = ["# 会議文字起こし"]
    if meta.get("single_file"):
        lines.append(f"# 元ファイル : {items[0][0].name}")
    else:
        lines.append(f"# 録音日時 : {meta.get('started_at', '?')}")
        lines.append(f"# 録音長   : {hhmmss(meta.get('wall_duration_sec', 0))}")
    lines += [f"# モデル   : {model_name}", ""]

    for s in all_segments:
        speaker = s["speaker"]
        lines.append(f"[{hhmmss(s['start'])}] "
                     + (f"{speaker}: " if speaker else "") + s["text"])
    transcript_path = out_dir / f"{prefix}transcript.txt"
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_speaker = {}
    for s in all_segments:
        key = s["speaker"] or "(話者なし)"
        by_speaker[key] = by_speaker.get(key, 0) + 1

    print()
    print("=" * 68)
    print(f"  {transcript_path}")
    print(f"  合計 {len(all_segments)} セグメント "
          + " / ".join(f"{k} {v}" for k, v in by_speaker.items()))
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
