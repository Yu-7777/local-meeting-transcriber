"""Linux (PulseAudio / PipeWire) のデバイス列挙・録音・着脱監視.

「相手の音」は sink の monitor ソースから取る。Ubuntu 22.04 の PulseAudio と
24.04 の PipeWire はどちらも libpulse のクライアント API を話すので、同じ
コードで両方に載る。依存を増やさないため libpulse.so.0 を ctypes で直接呼ぶ
（libpulse はデスクトップ環境に最初から入っている）。

WASAPI と違い、ここが吸収していること:

  - **時刻の作り方**  受け取ったバッファには、サーバ側に溜まっていた分の遅れが
    ある。読めた瞬間の時刻をそのまま使うと 2 本の時間軸が揃わない。最初の
    チャンクで録音開始時刻を割り出し、以降はそこからの累積サンプル数で時刻を
    決める（音は実時間で連続して流れてくるので、これが最も安定する）。
    実測とズレが RESYNC_SEC を超えた時だけ、本当に取りこぼしたとみなして
    時間軸を進め直し、呼び出し側に無音で埋めさせる。

  - **fragsize の指定**  指定しないとサーバ側に 2 秒近く溜まってから届く
    （実測は BUILD.md）。1 チャンクぶんに固定して、細かく受け取る。

  - 無音でもデータが実時間で流れてくる（WASAPI ループバックのように、
    何も再生されていない間に穴が空くことがない）。
"""

import ctypes
import sys
import threading
import time
from ctypes import (CFUNCTYPE, POINTER, Structure, byref, c_char_p, c_int,
                    c_uint8, c_uint32, c_uint64, c_void_p)

from . import common

APP_NAME = "会議録音・文字起こし"

# 接続とデバイス問い合わせの待ち上限（秒）。音声サーバが応答しない時に
# 画面が固まらないよう、必ず上限を設ける
CONNECT_TIMEOUT = 5.0
QUERY_TIMEOUT = 5.0

# 実測の時刻と、サンプル数から決めた時刻がこれ以上ずれたら「取りこぼした」と
# みなして時間軸を引き直す（秒）。定常時のばらつきより十分大きく、
# record.GAP_TOLERANCE より大きい値にする。根拠は BUILD.md
RESYNC_SEC = 0.30

PA_SAMPLE_S16LE = 3
PA_STREAM_RECORD = 2
PA_CONTEXT_READY = 4
PA_CONTEXT_FAILED = 5
PA_CONTEXT_TERMINATED = 6
PA_INVALID_INDEX = 0xFFFFFFFF
U32_MAX = 0xFFFFFFFF          # pa_buffer_attr の「既定に任せる」

# 監視する変化: sink / source の増減と状態、既定デバイスの変更 (SERVER)、
# カードの差し替え (CARD)。音量など録音に関係しない変化は購読しない
SUBSCRIBE_MASK = 0x0001 | 0x0002 | 0x0080 | 0x0200

try:
    _pa = ctypes.CDLL("libpulse.so.0")
    _pas = ctypes.CDLL("libpulse-simple.so.0")
except OSError as exc:      # 音声サーバの無い環境（コンテナ等）でも import は通す
    _pa = _pas = None
    _LOAD_ERROR = exc
else:
    _LOAD_ERROR = None


class _SampleSpec(Structure):
    _fields_ = [("format", c_int), ("rate", c_uint32), ("channels", c_uint8)]


class _ChannelMap(Structure):
    _fields_ = [("channels", c_uint8), ("map", c_int * 32)]


class _CVolume(Structure):
    _fields_ = [("channels", c_uint8), ("values", c_uint32 * 32)]


class _BufferAttr(Structure):
    _fields_ = [("maxlength", c_uint32), ("tlength", c_uint32),
                ("prebuf", c_uint32), ("minreq", c_uint32),
                ("fragsize", c_uint32)]


# 以下 2 つは introspect.h の並び順そのまま。必要な先頭部分だけを写している
# （末尾は読まないので省いてよいが、途中の順番を崩すと別の値を読んでしまう）。
class _SinkInfo(Structure):
    _fields_ = [("name", c_char_p), ("index", c_uint32),
                ("description", c_char_p), ("sample_spec", _SampleSpec),
                ("channel_map", _ChannelMap), ("owner_module", c_uint32),
                ("volume", _CVolume), ("mute", c_int),
                ("monitor_source", c_uint32), ("monitor_source_name", c_char_p)]


class _SourceInfo(Structure):
    _fields_ = [("name", c_char_p), ("index", c_uint32),
                ("description", c_char_p), ("sample_spec", _SampleSpec),
                ("channel_map", _ChannelMap), ("owner_module", c_uint32),
                ("volume", _CVolume), ("mute", c_int),
                ("monitor_of_sink", c_uint32),
                ("monitor_of_sink_name", c_char_p)]


class _ServerInfo(Structure):
    _fields_ = [("user_name", c_char_p), ("host_name", c_char_p),
                ("server_version", c_char_p), ("server_name", c_char_p),
                ("sample_spec", _SampleSpec),
                ("default_sink_name", c_char_p),
                ("default_source_name", c_char_p),
                ("cookie", c_uint32), ("channel_map", _ChannelMap)]


_STATE_CB = CFUNCTYPE(None, c_void_p, c_void_p)
_SUBSCRIBE_CB = CFUNCTYPE(None, c_void_p, c_uint32, c_uint32, c_void_p)
_SUCCESS_CB = CFUNCTYPE(None, c_void_p, c_int, c_void_p)
_SERVER_CB = CFUNCTYPE(None, c_void_p, POINTER(_ServerInfo), c_void_p)
_SINK_CB = CFUNCTYPE(None, c_void_p, POINTER(_SinkInfo), c_int, c_void_p)
_SOURCE_CB = CFUNCTYPE(None, c_void_p, POINTER(_SourceInfo), c_int, c_void_p)

if _LOAD_ERROR is None:
    # argtypes を省くとポインタが 32bit に切り詰められる（64bit で必須）
    for _name, _restype, _argtypes in (
        ("pa_threaded_mainloop_new", c_void_p, []),
        ("pa_threaded_mainloop_get_api", c_void_p, [c_void_p]),
        ("pa_threaded_mainloop_start", c_int, [c_void_p]),
        ("pa_threaded_mainloop_stop", None, [c_void_p]),
        ("pa_threaded_mainloop_free", None, [c_void_p]),
        ("pa_threaded_mainloop_lock", None, [c_void_p]),
        ("pa_threaded_mainloop_unlock", None, [c_void_p]),
        ("pa_context_new", c_void_p, [c_void_p, c_char_p]),
        ("pa_context_set_state_callback", None, [c_void_p, _STATE_CB, c_void_p]),
        ("pa_context_connect", c_int, [c_void_p, c_char_p, c_int, c_void_p]),
        ("pa_context_get_state", c_int, [c_void_p]),
        ("pa_context_disconnect", None, [c_void_p]),
        ("pa_context_unref", None, [c_void_p]),
        ("pa_context_get_server_info", c_void_p, [c_void_p, _SERVER_CB, c_void_p]),
        ("pa_context_get_sink_info_list", c_void_p, [c_void_p, _SINK_CB, c_void_p]),
        ("pa_context_get_source_info_list", c_void_p, [c_void_p, _SOURCE_CB, c_void_p]),
        ("pa_context_set_subscribe_callback", None,
         [c_void_p, _SUBSCRIBE_CB, c_void_p]),
        ("pa_context_subscribe", c_void_p,
         [c_void_p, c_uint32, _SUCCESS_CB, c_void_p]),
        ("pa_operation_unref", None, [c_void_p]),
        ("pa_strerror", c_char_p, [c_int]),
    ):
        _f = getattr(_pa, _name)
        _f.restype, _f.argtypes = _restype, _argtypes

    _pas.pa_simple_new.restype = c_void_p
    _pas.pa_simple_new.argtypes = [c_char_p, c_char_p, c_int, c_char_p, c_char_p,
                                   POINTER(_SampleSpec), c_void_p,
                                   POINTER(_BufferAttr), POINTER(c_int)]
    _pas.pa_simple_read.restype = c_int
    _pas.pa_simple_read.argtypes = [c_void_p, c_void_p, ctypes.c_size_t,
                                    POINTER(c_int)]
    _pas.pa_simple_get_latency.restype = c_uint64
    _pas.pa_simple_get_latency.argtypes = [c_void_p, POINTER(c_int)]
    _pas.pa_simple_free.restype = None
    _pas.pa_simple_free.argtypes = [c_void_p]


def _require():
    if _LOAD_ERROR is not None:
        raise RuntimeError(
            "PulseAudio のライブラリ (libpulse) が見つかりません。"
            f"({_LOAD_ERROR})")


def _strerror(code):
    text = _pa.pa_strerror(code)
    return text.decode("utf-8", "replace") if text else f"エラー {code}"


class _Connection:
    """libpulse への接続 1 本。問い合わせとイベント購読で共有する.

    コールバックは libpulse のスレッドから呼ばれる。呼び出し側のスレッドから
    context を触る時は、必ず mainloop のロックを取ること（libpulse の約束）。
    """

    def __init__(self, label="", timeout=CONNECT_TIMEOUT):
        _require()
        self._ml = None
        self._ctx = None
        self._ready = threading.Event()
        self._failed = False
        self._subscribe_cb = None    # 参照を保持（GC されると通知が届かない）

        name = f"{APP_NAME} {label}".strip()
        self._ml = _pa.pa_threaded_mainloop_new()
        self._ctx = _pa.pa_context_new(
            _pa.pa_threaded_mainloop_get_api(self._ml), name.encode("utf-8"))
        self._state_cb = _STATE_CB(self._on_state)
        _pa.pa_context_set_state_callback(self._ctx, self._state_cb, None)
        _pa.pa_context_connect(self._ctx, None, 0, None)
        _pa.pa_threaded_mainloop_start(self._ml)
        if not self._ready.wait(timeout) or self._failed:
            self.close()
            raise RuntimeError(
                "音声サーバ (PulseAudio / PipeWire) に接続できませんでした。")

    def _on_state(self, ctx, userdata):
        state = _pa.pa_context_get_state(ctx)
        if state == PA_CONTEXT_READY:
            self._ready.set()
        elif state in (PA_CONTEXT_FAILED, PA_CONTEXT_TERMINATED):
            self._failed = True
            self._ready.set()

    def close(self):
        if self._ml is not None:
            _pa.pa_threaded_mainloop_stop(self._ml)
        if self._ctx is not None:
            _pa.pa_context_disconnect(self._ctx)
            _pa.pa_context_unref(self._ctx)
            self._ctx = None
        if self._ml is not None:
            _pa.pa_threaded_mainloop_free(self._ml)
            self._ml = None

    def _query(self, start, timeout=QUERY_TIMEOUT):
        """問い合わせを 1 つ投げ、結果が出揃うまで待つ.

        start(ctx, done) が pa_operation* を返すこと。done.set() されるまで待つ。
        """
        done = threading.Event()
        _pa.pa_threaded_mainloop_lock(self._ml)
        try:
            op = start(self._ctx, done)
            if op:
                _pa.pa_operation_unref(op)
        finally:
            _pa.pa_threaded_mainloop_unlock(self._ml)
        if not done.wait(timeout):
            raise RuntimeError("音声サーバが応答しませんでした。")

    def defaults(self):
        """(既定 sink 名, 既定 source 名) を返す."""
        out = {}

        def start(ctx, done):
            def got(c, info, userdata):
                i = info.contents
                out["sink"] = _text(i.default_sink_name)
                out["source"] = _text(i.default_source_name)
                done.set()
            start.cb = _SERVER_CB(got)     # コールバック中は参照を残す
            return _pa.pa_context_get_server_info(ctx, start.cb, None)

        self._query(start)
        return out.get("sink"), out.get("source")

    def sinks(self):
        """出力デバイス（スピーカー等）を並び順のまま返す."""
        out = []

        def start(ctx, done):
            def got(c, info, eol, userdata):
                if eol:
                    done.set()
                    return
                i = info.contents
                out.append({
                    "index": i.index,
                    "name": _text(i.name),
                    "description": _text(i.description) or _text(i.name),
                    "monitor_source_name": _text(i.monitor_source_name),
                    "rate": i.sample_spec.rate,
                    "channels": i.sample_spec.channels,
                })
            start.cb = _SINK_CB(got)
            return _pa.pa_context_get_sink_info_list(ctx, start.cb, None)

        self._query(start)
        return out

    def sources(self):
        """入力ソース（マイクと monitor）を並び順のまま返す."""
        out = []

        def start(ctx, done):
            def got(c, info, eol, userdata):
                if eol:
                    done.set()
                    return
                i = info.contents
                monitor = (None if i.monitor_of_sink == PA_INVALID_INDEX
                           else _text(i.monitor_of_sink_name))
                out.append({
                    "index": i.index,
                    "name": _text(i.name),
                    "description": _text(i.description) or _text(i.name),
                    "monitor_of": monitor,
                    "rate": i.sample_spec.rate,
                    "channels": i.sample_spec.channels,
                })
            start.cb = _SOURCE_CB(got)
            return _pa.pa_context_get_source_info_list(ctx, start.cb, None)

        self._query(start)
        return out

    def subscribe(self, mask, on_event):
        """デバイスの変化を購読する。on_event は libpulse のスレッドから呼ばれる."""
        self._subscribe_cb = _SUBSCRIBE_CB(
            lambda ctx, event, index, userdata: on_event())
        _pa.pa_threaded_mainloop_lock(self._ml)
        try:
            _pa.pa_context_set_subscribe_callback(
                self._ctx, self._subscribe_cb, None)
            op = _pa.pa_context_subscribe(self._ctx, mask, _SUCCESS_CB(0), None)
            if op:
                _pa.pa_operation_unref(op)
        finally:
            _pa.pa_threaded_mainloop_unlock(self._ml)


def _text(value):
    return value.decode("utf-8", "replace") if value else ""


def _device_info(source, display):
    """PyAudio が返す dict の形に合わせる（呼び出し側を共通にするため）."""
    return {
        "index": source["index"],
        "name": display,
        "defaultSampleRate": source["rate"],
        "maxInputChannels": source["channels"],
        "isLoopbackDevice": source["monitor_of"] is not None,
        # 録音時に開くための PulseAudio 側の識別子（この層の外では使わない）
        "source": source["name"],
    }


class _Timeline:
    """受け取ったチャンクに時刻を割り当てる（冒頭の「時刻の作り方」参照）.

    実測時刻をそのまま使うとバッファの揺らぎがそのまま時間軸の揺らぎになるので、
    最初のチャンクから割り出した開始時刻＋累積サンプル数で決める。
    """

    def __init__(self, rate, frames_per_chunk, resync_sec=RESYNC_SEC):
        self._rate = rate
        self._frames_per_chunk = frames_per_chunk
        self._resync_sec = resync_sec
        self._anchor = None
        self._frames = 0

    def stamp(self, measured):
        """1 チャンク届いた実測時刻を渡すと (使うべき時刻, 取りこぼしたか) を返す."""
        self._frames += self._frames_per_chunk
        elapsed = self._frames / self._rate
        if self._anchor is None:
            self._anchor = measured - elapsed
        ts = self._anchor + elapsed
        lost = measured - ts
        if lost > self._resync_sec:
            # 本当に取りこぼした。時間軸を進めて、空いた分は呼び出し側に
            # 無音で埋めさせる（そうしないと以降ずっと前にずれる）
            self._anchor += lost
            return measured, True
        return ts, False


class Stream:
    """1 本の入力ストリーム。読み取りスレッドが on_chunk へ渡し続ける."""

    def __init__(self, info, rate, channels, chunk, on_chunk):
        _require()
        self._on_chunk = on_chunk
        self._rate = rate
        self._frames = chunk
        self._size = chunk * 2 * channels     # 16bit
        self._stop = threading.Event()
        self._thread = None

        spec = _SampleSpec(PA_SAMPLE_S16LE, rate, channels)
        # fragsize だけ指定する。maxlength を既定のままにするのは、こちらの
        # 読み出しが一瞬遅れてもサーバ側で捨てられないようにするため
        attr = _BufferAttr(U32_MAX, U32_MAX, U32_MAX, U32_MAX, self._size)
        err = c_int()
        self._handle = _pas.pa_simple_new(
            None, APP_NAME.encode("utf-8"), PA_STREAM_RECORD,
            info["source"].encode("utf-8"), info["name"].encode("utf-8"),
            byref(spec), None, byref(attr), byref(err))
        if not self._handle:
            raise RuntimeError(
                f"{info['name']} を開けませんでした: {_strerror(err.value)}")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _latency(self):
        """サーバ側に溜まっている分の遅れ（秒）。取れなければ 0."""
        err = c_int()
        usec = _pas.pa_simple_get_latency(self._handle, byref(err))
        if usec == 0xFFFFFFFFFFFFFFFF:
            return 0.0
        return usec / 1e6

    def _run(self):
        buf = (ctypes.c_char * self._size)()
        err = c_int()
        timeline = _Timeline(self._rate, self._frames)
        while not self._stop.is_set():
            if _pas.pa_simple_read(self._handle, buf, self._size, byref(err)) < 0:
                break       # デバイスが消えた等。メーターが止まるので気付ける
            ts, overflow = timeline.stamp(time.perf_counter() - self._latency())
            self._on_chunk(ts, bytes(buf), overflow)

    def stop(self):
        self._stop.set()

    def close(self):
        self.stop()
        if self._thread is not None:
            # 読み取りは 1 チャンク（20ms 前後）で返るので、すぐ抜ける
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._handle:
            _pas.pa_simple_free(self._handle)
            self._handle = None


class AudioSystem:
    """PulseAudio / PipeWire への接続."""

    def __init__(self):
        self._conn = _Connection()

    def close(self):
        self._conn.close()

    def _devices(self):
        """(ループバック, マイク, 既定ループバック index, 既定マイク index).

        monitor の表示名には元の出力デバイス名を使う。"Monitor of X" という
        説明文は環境によって訳が変わるので、sink 側の名前を引き当てる。
        """
        default_sink, default_source = self._conn.defaults()
        sink_desc = {s["name"]: s["description"] for s in self._conn.sinks()}

        loopbacks, mics = [], []
        default_lb = default_mic = None
        for src in self._conn.sources():
            if src["monitor_of"]:
                display = sink_desc.get(src["monitor_of"], src["description"])
                info = _device_info(src, display)
                loopbacks.append(info)
                if src["monitor_of"] == default_sink:
                    default_lb = info["index"]
            else:
                info = _device_info(src, src["description"])
                mics.append(info)
                if src["name"] == default_source:
                    default_mic = info["index"]
        return loopbacks, mics, default_lb, default_mic

    def resolve_loopback(self, index=None):
        loopbacks, _, default_lb, _ = self._devices()
        if index is not None:
            for info in loopbacks:
                if info["index"] == index:
                    return info
            raise SystemExit(
                f"index {index} はループバックデバイスではありません。\n"
                f"{common.cli_hint('devices')} の"
                "「ループバックデバイス」欄から選んでください。"
            )
        for info in loopbacks:
            if info["index"] == default_lb:
                return info
        if loopbacks:
            return loopbacks[0]
        raise SystemExit(
            "ループバックデバイスが見つかりませんでした。"
            f"{common.cli_hint('devices')} で確認してください。"
        )

    def resolve_mic(self, index=None):
        _, mics, _, default_mic = self._devices()
        if index is not None:
            for info in mics:
                if info["index"] == index:
                    return info
            raise SystemExit(
                f"index {index} のマイクが見つかりません。"
                f"{common.cli_hint('devices')} で確認してください。")
        for info in mics:
            if info["index"] == default_mic:
                return info
        if mics:
            return mics[0]
        raise SystemExit(
            "マイクが見つかりませんでした。"
            f"{common.cli_hint('devices')} で確認してください。")

    def list_devices(self):
        """(ループバック一覧, マイク一覧) を返す。要素は (表示名, index)."""
        loopbacks, mics, default_lb, default_mic = self._devices()
        return ([(f"{i['name']}{' ★既定' if i['index'] == default_lb else ''}",
                  i["index"]) for i in loopbacks],
                [(f"{i['name']}{' ★既定' if i['index'] == default_mic else ''}",
                  i["index"]) for i in mics])

    def open_stream(self, info, rate, channels, chunk, on_chunk):
        return Stream(info, rate, channels, chunk, on_chunk)


class DeviceWatcher:
    """音声デバイスの着脱・既定変更をイベントで監視する（ポーリングしない）.

    on_change は libpulse のスレッドから呼ばれるため、Tkinter を直接操作せず、
    呼び出し側で thread-safe なキュー等に積んでからメインスレッドへ渡すこと。
    """

    def __init__(self, on_change):
        self._on_change = on_change
        self._conn = None

    def start(self, timeout=CONNECT_TIMEOUT):
        """監視を開始する。準備に失敗したら例外を投げる（呼び出し側で握り潰す想定）."""
        conn = _Connection("(監視)", timeout=timeout)
        conn.subscribe(SUBSCRIBE_MASK, self._notify)
        self._conn = conn   # 参照を保持（GC されると通知が届かなくなる）

    def _notify(self):
        try:
            self._on_change()
        except Exception:
            pass   # libpulse のスレッドへ例外を投げ返さない


def _fmt(index, name, detail, mark=""):
    return f"  [{index:>3}] {mark:<2}{name}\n        {detail}"


def print_devices():
    """診断用にデバイスを一覧表示する（app.py devices）."""
    try:
        conn = _Connection()
    except RuntimeError as exc:
        print(f"{exc} 音声サーバの状態を確認してください。", file=sys.stderr)
        return 1
    try:
        default_sink, default_source = conn.defaults()
        sinks = conn.sinks()
        sources = conn.sources()
    finally:
        conn.close()
    sink_desc = {s["name"]: s["description"] for s in sinks}

    print("=" * 70)
    print("出力デバイス (スピーカー/ヘッドフォン)")
    print("=" * 70)
    for s in sinks:
        detail = f"out={s['channels']}ch rate={s['rate']}Hz"
        print(_fmt(s["index"], s["description"], detail,
                   "*" if s["name"] == default_sink else ""))

    print()
    print("=" * 70)
    print("ループバックデバイス (= PC から流れる音 / 相手の声)")
    print("=" * 70)
    monitors = [s for s in sources if s["monitor_of"]]
    for s in monitors:
        detail = f"in={s['channels']}ch rate={s['rate']}Hz"
        print(_fmt(s["index"], sink_desc.get(s["monitor_of"], s["description"]),
                   detail, "*" if s["monitor_of"] == default_sink else ""))
    if not monitors:
        print("  (見つかりません)")

    print()
    print("=" * 70)
    print("入力デバイス (マイク)")
    print("=" * 70)
    for s in sources:
        if s["monitor_of"]:
            continue
        detail = f"in={s['channels']}ch rate={s['rate']}Hz"
        print(_fmt(s["index"], s["description"], detail,
                   "*" if s["name"] == default_source else ""))

    print()
    print("* = 既定のデバイス")
    print("既定以外を使う場合: "
          f"{common.cli_hint('record')} --mic <index> --loopback <index>")
    return 0
