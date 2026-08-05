"""PC の出力音声(相手の声)と自分のマイクを、別々の WAV に同時録音する.

WASAPI ループバックを使うため、Zoom / Google Meet など会議ツールの種類や
ホスト権限に一切依存しない。録音した 2 本は transcribe.py で文字起こしする。

使い方:
    python record.py                      # 既定デバイスで録音、Enter で停止
    python record.py --mic 24             # マイクを指定 (check_devices.py で確認)
"""

import argparse
import datetime as dt
import json
import queue
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

CHUNK = 1024
SAMPLE_WIDTH = 2  # paInt16
START_TIMEOUT = 10.0  # 全ストリームが動き出すのを待つ上限(秒)
GAP_TOLERANCE = 0.15  # これを超える時間軸のズレを無音で埋める(秒)

# 録音フォルダは「日時[_名前]」。日時が先頭なのは、名前順に並べた時に
# 時系列順になるため。秒は入れない（読みにくいわりに区別に使わない）。
STAMP_FORMAT = "%Y_%m_%d_%H_%M"
# 上の形式と、旧形式 (20260802_174653) の両方を日時として認識する
STAMP_RE = re.compile(r"^(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}|\d{8}_\d{6})(?:_(.*))?$")
# Windows のファイル名に使えない文字（: はドライブ区切りのため使えない）
INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME_LEN = 40

import config  # noqa: E402


def sanitize_name(name):
    """録音名をフォルダ名に使える形に整える。使えない場合は空文字を返す."""
    cleaned = INVALID_CHARS.sub("_", str(name or "")).strip()
    # 末尾のドットと空白は Windows が黙って落とすので、こちらで取り除く
    return cleaned[:MAX_NAME_LEN].strip(". ")


def split_recording_name(folder_name):
    """フォルダ名を (日時部分, 名前部分) に分ける.

    日時として読めない場合は、全体を日時部分として扱う（改名しても
    元の名前を失わないようにするため）。
    """
    m = STAMP_RE.match(folder_name)
    if m:
        return m.group(1), m.group(2) or ""
    return folder_name, ""


def _unique_dir(parent, stem, exclude=None):
    """parent/stem が既にあれば連番を足して、空いている名前を返す."""
    candidate = parent / stem
    n = 2
    while candidate.exists() and candidate != exclude:
        candidate = parent / f"{stem}_{n}"
        n += 1
    return candidate


def new_recording_dir(base, name="", now=None):
    """次の録音を入れるフォルダのパスを決める（作成はしない）.

    秒を持たないため、同じ分に録り直すと同名になる。既存の録音を
    上書きしないよう、その場合は連番を足す。
    """
    stem = (now or dt.datetime.now()).strftime(STAMP_FORMAT)
    label = sanitize_name(name)
    if label:
        stem = f"{stem}_{label}"
    return _unique_dir(Path(base), stem)


def rename_recording(path, name):
    """録音フォルダの名前部分だけを付け替える。新しいパスを返す.

    日時部分は変えない（時系列の並びを壊さないため）。name が空なら
    名前を外して日時だけに戻す。
    """
    path = Path(path)
    stamp, _ = split_recording_name(path.name)
    label = sanitize_name(name)
    stem = f"{stamp}_{label}" if label else stamp
    target = _unique_dir(path.parent, stem, exclude=path)
    if target != path:
        path.rename(target)
    return target


def recording_size(path):
    """録音フォルダの合計バイト数を返す."""
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def move_to_trash(path):
    """フォルダをごみ箱へ移す.

    完全削除ではなくごみ箱にするのは、置き換える対象がエクスプローラーでの
    削除であり、そちらと同じ挙動にするのが最も驚きが少ないため。会議の録音は
    取り直しがきかないので、誤操作から戻せることも重要。
    """
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040  # これがごみ箱行きにするフラグ
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    # pFrom は「NUL 区切り + 末尾に NUL がもう 1 つ」という形式
    op.pFrom = str(path) + "\0\0"
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"ごみ箱へ移せませんでした (コード {result}): {path}")
    if op.fAnyOperationsAborted:
        raise OSError(f"削除が中断されました: {path}")


class StreamRecorder:
    """1 本の入力ストリームを WAV に書き出す.

    コールバックはリアルタイムスレッドで走るため、そこではキューに積むだけにし、
    ファイル I/O は専用スレッドに逃がす（ドロップアウト防止）。
    """

    def __init__(self, name, label, device_info, path, barrier, base_t0_box):
        self.name = name
        self.label = label
        self.device = device_info
        self.path = path
        self.rate = int(device_info["defaultSampleRate"])
        self.channels = max(1, min(int(device_info["maxInputChannels"]), 2))

        self.queue = queue.Queue()
        self.frames_written = 0
        self.level = 0.0
        self.t0 = None
        self.pad_sec = 0.0     # 開始遅延ぶんの先頭無音
        self.filled_sec = 0.0  # 途中の欠落を埋めた無音の合計
        self.overflows = 0
        self.stream = None
        self._stop_time = None
        self._barrier = barrier
        self._base_t0_box = base_t0_box
        self._thread = None
        self._wave = None

    # --- PortAudio コールバック（リアルタイムスレッド） -------------------
    def _callback(self, in_data, frame_count, time_info, status):
        now = time.perf_counter()
        if self.t0 is None:
            self.t0 = now - frame_count / self.rate  # このチャンクの先頭時刻
        if status:
            self.overflows += 1
        self.queue.put((now, in_data))
        return (None, pyaudio.paContinue)

    def _write_silence(self, frames):
        """無音を frames サンプル分だけ書き込む."""
        frame_bytes = SAMPLE_WIDTH * self.channels
        block = b"\x00" * (CHUNK * frame_bytes)
        remaining = frames
        while remaining > 0:
            n = min(CHUNK, remaining)
            self._wave.writeframes(block[: n * frame_bytes])
            remaining -= n
        self.frames_written += frames

    # --- 書き出しスレッド -------------------------------------------------
    def _writer(self):
        # 最初のチャンクを待つ。これが返った時点で self.t0 が確定している。
        item = self.queue.get()

        # 全ストリームの開始時刻が出揃うのを待ってから基準時刻を確定させる。
        self._barrier.wait()
        base_t0 = self._base_t0_box[0]
        if base_t0 is None:  # 開始に失敗した場合の保険
            base_t0 = self.t0 if self.t0 is not None else time.perf_counter()

        frame_bytes = SAMPLE_WIDTH * self.channels
        tolerance = int(GAP_TOLERANCE * self.rate)
        first = True

        while item is not None:
            ts, chunk = item
            n_frames = len(chunk) // frame_bytes
            chunk_start = ts - n_frames / self.rate

            # 本来この位置にあるべきサンプル数と、実際に書いた数を突き合わせる。
            # WASAPI ループバックは何も再生されていない間データを返さないため、
            # 会議の沈黙中に穴が空く。そのぶんを無音で埋めて時間軸を保つ。
            expected = int(round((chunk_start - base_t0) * self.rate))
            gap = expected - self.frames_written
            if gap > tolerance:
                self._write_silence(gap)
                if first:
                    self.pad_sec = gap / self.rate
                else:
                    self.filled_sec += gap / self.rate
            first = False

            self._wave.writeframes(chunk)
            self.frames_written += n_frames
            samples = np.frombuffer(chunk, dtype=np.int16)
            if samples.size:
                rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
                self.level = float(rms) / 32768.0

            item = self.queue.get()

        # 末尾の穴埋め。最後の発話以降ずっと無音だった場合、ループバックは
        # 何も返さないままなので、停止時刻まで無音を足して長さを揃える。
        if self._stop_time is not None:
            target = int(round((self._stop_time - base_t0) * self.rate))
            gap = target - self.frames_written
            if gap > tolerance:
                self._write_silence(gap)
                self.filled_sec += gap / self.rate

    def start(self, p):
        self._wave = wave.open(str(self.path), "wb")
        self._wave.setnchannels(self.channels)
        self._wave.setsampwidth(SAMPLE_WIDTH)
        self._wave.setframerate(self.rate)

        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()

        self.stream = p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=self.device["index"],
            frames_per_buffer=CHUNK,
            stream_callback=self._callback,
        )

    def stop(self):
        if self._stop_time is None:
            self._stop_time = time.perf_counter()
        if self.stream is not None:
            try:
                self.stream.stop_stream()
            finally:
                self.stream.close()
                self.stream = None
        if self._thread is not None:
            self.queue.put(None)          # 番兵：残りを書き切ってから終了
            self._thread.join(timeout=30)
            self._thread = None
        if self._wave is not None:
            self._wave.close()
            self._wave = None

    @property
    def recorded_sec(self):
        return self.frames_written / self.rate if self.rate else 0.0


def bar(level, width=16):
    """RMS(0..1) を dB スケールのバーにする."""
    db = 20 * np.log10(level) if level > 1e-6 else -120.0
    filled = int(np.clip((db + 60.0) / 60.0, 0.0, 1.0) * width)
    return "█" * filled + "░" * (width - filled), db


def hhmmss(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def resolve_loopback(p, index):
    if index is not None:
        info = p.get_device_info_by_index(index)
        if not info.get("isLoopbackDevice", False):
            raise SystemExit(
                f"index {index} はループバックデバイスではありません。\n"
                "check_devices.py の「ループバックデバイス」欄から選んでください。"
            )
        return info
    try:
        return p.get_default_wasapi_loopback()
    except Exception:
        pass
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    for lb in p.get_loopback_device_info_generator():
        if speakers["name"] in lb["name"]:
            return lb
    raise SystemExit(
        "ループバックデバイスが見つかりませんでした。check_devices.py で確認してください。"
    )


def resolve_mic(p, index):
    if index is not None:
        return p.get_device_info_by_index(index)
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])


class RecordingSession:
    """録音一式（デバイス解決・時間軸の同期・meta.json 書き出し）をまとめたもの.

    CLI (record.py) と GUI (gui.py) の双方から使う。同期処理を二重に持たないため。
    """

    def __init__(self, mic_index=None, loopback_index=None, outdir=None, name=""):
        self.outdir = Path(outdir) if outdir else new_recording_dir(
            config.recordings_dir(), name)
        self._mic_index = mic_index
        self._loopback_index = loopback_index
        self._barrier = threading.Event()
        self._base_t0_box = [None]
        self._p = None
        self._wall_start = None
        self._stopped = False
        self.recorders = []
        self.started_at = None
        self.wall_duration = 0.0
        self.meta = None

    def start(self):
        self.outdir.mkdir(parents=True, exist_ok=True)
        self._p = pyaudio.PyAudio()
        loopback = resolve_loopback(self._p, self._loopback_index)
        mic = resolve_mic(self._p, self._mic_index)
        self.recorders = [
            StreamRecorder("system", "相手", loopback, self.outdir / "system.wav",
                           self._barrier, self._base_t0_box),
            StreamRecorder("mic", "自分", mic, self.outdir / "mic.wav",
                           self._barrier, self._base_t0_box),
        ]
        self.started_at = dt.datetime.now().astimezone()
        self._wall_start = time.perf_counter()
        for r in self.recorders:
            r.start(self._p)
        # 基準時刻の確定は別スレッドに逃がす（GUI を固めないため）
        threading.Thread(target=self._settle, daemon=True).start()

    def _settle(self):
        deadline = time.perf_counter() + START_TIMEOUT
        while time.perf_counter() < deadline:
            if all(r.t0 is not None for r in self.recorders):
                break
            time.sleep(0.01)
        live = [r.t0 for r in self.recorders if r.t0 is not None]
        self._base_t0_box[0] = min(live) if live else self._wall_start
        self._barrier.set()

    @property
    def elapsed(self):
        if self._wall_start is None:
            return 0.0
        if self._stopped:
            return self.wall_duration
        return time.perf_counter() - self._wall_start

    @property
    def pending(self):
        """まだ音を返していないストリームのラベル（許可待ち等の検出用）."""
        return [r.label for r in self.recorders if r.t0 is None]

    def stop(self):
        """録音を止め、meta.json を書いて内容を返す."""
        if self._stopped:
            return self.meta
        self._stopped = True
        self.wall_duration = time.perf_counter() - self._wall_start
        self._barrier.set()
        for r in self.recorders:
            r.stop()

        self.meta = {
            "started_at": self.started_at.isoformat(),
            "wall_duration_sec": round(self.wall_duration, 3),
            "aligned": True,  # 各 WAV は無音を詰めて同一時間軸に揃えてある
            "streams": {},
        }
        for r in self.recorders:
            self.meta["streams"][r.name] = {
                "file": r.path.name,
                "label": r.label,
                "device": r.device["name"],
                "rate": r.rate,
                "channels": r.channels,
                "start_delay_sec": round(r.pad_sec, 3),
                "gap_filled_sec": round(r.filled_sec, 3),
                "recorded_sec": round(r.recorded_sec, 3),
            }
        (self.outdir / "meta.json").write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.meta

    def close(self):
        self._barrier.set()
        for r in self.recorders:
            try:
                r.stop()
            except Exception:
                pass
        if self._p is not None:
            self._p.terminate()
            self._p = None

    def summary_lines(self):
        """終了後の要約（CLI・GUI 共通の文言）."""
        lines = [f"録音時間 (実測): {hhmmss(self.wall_duration)}"]
        for r in self.recorders:
            size_mb = r.path.stat().st_size / (1024 * 1024) if r.path.exists() else 0.0
            drift = r.recorded_sec - self.wall_duration
            warn = "  <-- 取りこぼしの可能性" if abs(drift) > 2.0 else ""
            lines.append(
                f"{r.label}: {hhmmss(r.recorded_sec)} "
                f"({size_mb:.1f} MB, ズレ {drift:+.2f}s{warn})"
            )
            if r.pad_sec > 0.5:
                lines.append(f"    起動待ち {r.pad_sec:.1f}s（先頭に無音を詰めて同期済み）")
            if r.filled_sec > 0.5:
                lines.append(f"    無音区間 {r.filled_sec:.1f}s を補完（時間軸は維持）")
            if r.overflows:
                lines.append(f"    ※ バッファ警告 {r.overflows} 回（音が途切れた可能性）")
            if r.recorded_sec < 1.0:
                lines.append("    ※ ほとんど録れていません。デバイス選択を確認してください")
        return lines


def main():
    ap = argparse.ArgumentParser(description="会議音声をローカル録音する")
    ap.add_argument("--mic", type=int, default=None, help="マイクのデバイス index")
    ap.add_argument("--loopback", type=int, default=None, help="ループバックの device index")
    ap.add_argument("--outdir", type=Path, default=None, help="出力先ディレクトリ")
    ap.add_argument("--name", default="",
                    help="録音の名前。フォルダ名が 日時_名前 になる（任意）")
    ap.add_argument("--seconds", type=float, default=None, help="指定秒数で自動停止")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    session = RecordingSession(args.mic, args.loopback, args.outdir, args.name)
    recorders = []
    try:
        session.start()
        recorders = session.recorders

        print("=" * 68)
        for r in recorders:
            print(f"  {r.label} : {r.device['name']}")
            print(f"         {r.rate}Hz {r.channels}ch  ->  {r.path.name}")
        print("=" * 68)
        print(f"  保存先: {session.outdir}")
        print()

        wall_start = time.perf_counter()
        stop_event = threading.Event()

        def wait_for_enter():
            try:
                input()
            except (EOFError, OSError):
                return  # 非対話実行時は Ctrl+C のみで停止
            stop_event.set()

        threading.Thread(target=wait_for_enter, daemon=True).start()

        if args.seconds:
            print(f"録音中です。{args.seconds:.0f} 秒で自動停止します (Enter / Ctrl+C で即停止)")
        else:
            print("録音中です。停止するには Enter を押してください (Ctrl+C でも可)")
        print()
        try:
            while not stop_event.is_set():
                if args.seconds and (time.perf_counter() - wall_start) >= args.seconds:
                    break
                cells = []
                for r in recorders:
                    graph, db = bar(r.level)
                    cells.append(f"{r.label} |{graph}| {db:6.1f}dB")
                print(f"\r  {hhmmss(session.elapsed)}  " + "   ".join(cells) + "  ",
                      end="", flush=True)
                time.sleep(0.15)
        except KeyboardInterrupt:
            pass

        print("\n\n停止しています...")
        session.stop()

        print()
        print("=" * 68)
        for line in session.summary_lines():
            print("  " + line)
        print("=" * 68)
        print(f"\n  保存先: {session.outdir}")
        print("  文字起こし: .venv\\Scripts\\python.exe transcribe.py")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
