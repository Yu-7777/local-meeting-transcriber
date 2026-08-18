"""Windows (WASAPI) のデバイス列挙・録音・着脱監視.

「相手の音」は WASAPI のループバックデバイスから取る。デバイスの着脱と既定の
変更は Core Audio の通知 (IMMNotificationClient) で受ける。ポーリングしないので
変化が無い間のコストはほぼ無い（実測: list_devices() のポーリングは 1 回あたり
60〜75ms かかる）。

ここで使う COM インタフェース定義（IMMNotificationClient /
IMMDeviceEnumerator）は Windows SDK の mmdeviceapi.h をそのまま写したもの。
vtable はメソッドの並び順で決まるため、呼ばない関数も含めて本物と同じ順番で
定義する必要がある（順番を崩すと落ちる）。
"""

import sys
import threading
import time
from ctypes import HRESULT, POINTER, Structure
from ctypes.wintypes import DWORD, LPCWSTR

# MTA にしないとコールバックを受け取るのにメッセージポンプが要る。
# comtypes は最初に CoInitialize する時にだけこの値を見るので、
# import より前に置く（このプロセスで COM を使うのはここだけの想定）。
sys.coinit_flags = 0  # COINIT_MULTITHREADED

import comtypes  # noqa: E402
import pyaudiowpatch as pyaudio  # noqa: E402
from comtypes import COMMETHOD, COMObject, GUID, IUnknown  # noqa: E402

from . import common  # noqa: E402

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")

# 登録を外せなかった通知クライアント（DeviceWatcher.stop 参照）
_ORPHANS = []


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _IMMDevice(IUnknown):
    """呼び出さない。IMMDeviceEnumerator の型参照のためだけに存在."""
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = ()


class _IMMDeviceCollection(IUnknown):
    """呼び出さない。IMMDeviceEnumerator の型参照のためだけに存在."""
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = ()


class IMMNotificationClient(IUnknown):
    """Windows から呼ばれる側。5 個すべてを本来の順番で定義する."""
    _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
    _methods_ = (
        COMMETHOD([], HRESULT, "OnDeviceStateChanged",
                 (["in"], LPCWSTR, "pwstrDeviceId"),
                 (["in"], DWORD, "dwNewState")),
        COMMETHOD([], HRESULT, "OnDeviceAdded",
                 (["in"], LPCWSTR, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDeviceRemoved",
                 (["in"], LPCWSTR, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDefaultDeviceChanged",
                 (["in"], DWORD, "flow"),
                 (["in"], DWORD, "role"),
                 (["in"], LPCWSTR, "pwstrDefaultDeviceId")),
        COMMETHOD([], HRESULT, "OnPropertyValueChanged",
                 (["in"], LPCWSTR, "pwstrDeviceId"),
                 (["in"], PROPERTYKEY, "key")),
    )


class IMMDeviceEnumerator(IUnknown):
    """こちらから呼ぶ側。使うのは Register/Unregister だけだが、手前の
    3 個も vtable の位置合わせのために同じ順番で定義する（本文参照）."""
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = (
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                 (["in"], DWORD, "dataFlow"),
                 (["in"], DWORD, "dwStateMask"),
                 (["out"], POINTER(POINTER(_IMMDeviceCollection)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                 (["in"], DWORD, "dataFlow"),
                 (["in"], DWORD, "role"),
                 (["out"], POINTER(POINTER(_IMMDevice)), "ppDevice")),
        COMMETHOD([], HRESULT, "GetDevice",
                 (["in"], LPCWSTR, "pwstrId"),
                 (["out"], POINTER(POINTER(_IMMDevice)), "ppDevice")),
        COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
                 (["in"], POINTER(IMMNotificationClient), "pClient")),
        COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
                 (["in"], POINTER(IMMNotificationClient), "pClient")),
    )


class _Client(COMObject):
    _com_interfaces_ = [IMMNotificationClient]

    def __init__(self, on_change):
        super().__init__()
        self._on_change = on_change

    def _notify(self):
        try:
            self._on_change()
        except Exception:
            pass  # COM 境界を越えて例外を投げない

    def OnDeviceStateChanged(self, pwstrDeviceId, dwNewState):
        self._notify()

    def OnDeviceAdded(self, pwstrDeviceId):
        self._notify()

    def OnDeviceRemoved(self, pwstrDeviceId):
        self._notify()

    def OnDefaultDeviceChanged(self, flow, role, pwstrDefaultDeviceId):
        self._notify()

    def OnPropertyValueChanged(self, pwstrDeviceId, key):
        pass  # 音量などの細かい変更。デバイス構成の変化ではないので無視


class DeviceWatcher:
    """音声デバイスの着脱・既定変更をバックグラウンドで監視する.

    on_change は COM のコールバックスレッドから呼ばれるため、
    Tkinter を直接操作せず、呼び出し側で thread-safe なキュー等に
    積んでからメインスレッドへ渡すこと。
    """

    def __init__(self, on_change):
        self._on_change = on_change
        self._keep_alive = threading.Event()
        self._ready = threading.Event()
        self._failed = None
        self._enumerator = None
        self._client = None

    def start(self, timeout=5.0):
        """監視を開始する。準備に失敗したら例外を投げる（呼び出し側で握り潰す想定）."""
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(timeout)
        if self._failed is not None:
            raise self._failed

    def _run(self):
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                comtypes.CLSCTX_INPROC_SERVER)
            client = _Client(self._on_change)
            enumerator.RegisterEndpointNotificationCallback(client)
            # 参照を保持し続ける（GC されると通知が届かなくなる）
            self._enumerator, self._client = enumerator, client
        except Exception as exc:
            self._failed = exc
            self._ready.set()
            return
        self._ready.set()
        self._keep_alive.wait()  # stop() されるまで待つだけ（daemon なので join 不要）

    def stop(self):
        """監視をやめる。登録を外してからスレッドを終わらせる."""
        enumerator, client = self._enumerator, self._client
        self._enumerator = self._client = None
        self._keep_alive.set()
        if enumerator is None or client is None:
            return
        try:
            enumerator.UnregisterEndpointNotificationCallback(client)
        except Exception:
            # 外せなかった時に参照を捨てると、次の通知で解放済みの
            # オブジェクトを呼ばれる。落とさないほうを優先して残す
            _ORPHANS.append((enumerator, client))


class Stream:
    """1 本の入力ストリーム。届いたチャンクを on_chunk へ渡すだけ.

    PortAudio のコールバックはリアルタイムスレッドで走るので、ここでは
    時刻を打って渡すことしかしない（重い処理は呼び出し側の書き出しスレッド）。
    """

    def __init__(self, p, info, rate, channels, chunk, on_chunk):
        self._on_chunk = on_chunk
        self._stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=info["index"],
            frames_per_buffer=chunk,
            stream_callback=self._callback,
        )

    def _callback(self, in_data, frame_count, time_info, status):
        self._on_chunk(time.perf_counter(), in_data, bool(status))
        return (None, pyaudio.paContinue)

    def stop(self):
        self._stream.stop_stream()

    def close(self):
        self._stream.close()


class AudioSystem:
    """WASAPI への接続（PyAudio のインスタンス 1 つ）."""

    def __init__(self):
        self._p = pyaudio.PyAudio()

    def close(self):
        self._p.terminate()

    def resolve_loopback(self, index=None):
        p = self._p
        if index is not None:
            info = p.get_device_info_by_index(index)
            if not info.get("isLoopbackDevice", False):
                raise SystemExit(
                    f"index {index} はループバックデバイスではありません。\n"
                    f"{common.cli_hint('devices')} の"
                    "「ループバックデバイス」欄から選んでください。"
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
            "ループバックデバイスが見つかりませんでした。"
            f"{common.cli_hint('devices')} で確認してください。"
        )

    def resolve_mic(self, index=None):
        p = self._p
        if index is not None:
            return p.get_device_info_by_index(index)
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        return p.get_device_info_by_index(wasapi["defaultInputDevice"])

    def list_devices(self):
        """(ループバック一覧, マイク一覧) を返す。要素は (表示名, index)."""
        p = self._p
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        try:
            # 録音時と同じ手順で決める。一覧側が独自に選ぶと ★既定 の表示と
            # 実際に録音されるデバイスが食い違う
            default_lb = self.resolve_loopback(None)["index"]
        except (Exception, SystemExit):
            default_lb = None  # 決められなくても一覧は出す（手で選べる）
        default_mic = wasapi["defaultInputDevice"]

        loopbacks, mics = [], []
        for lb in p.get_loopback_device_info_generator():
            mark = " ★既定" if lb["index"] == default_lb else ""
            name = lb["name"].replace(" [Loopback]", "")
            loopbacks.append((f"{name}{mark}", lb["index"]))

        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"]:
                continue
            if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
                mark = " ★既定" if d["index"] == default_mic else ""
                mics.append((f"{d['name']}{mark}", d["index"]))
        return loopbacks, mics

    def open_stream(self, info, rate, channels, chunk, on_chunk):
        return Stream(self._p, info, rate, channels, chunk, on_chunk)


def _fmt(info, mark=""):
    return (
        f"  [{info['index']:>3}] {mark:<2}{info['name']}\n"
        f"        in={info['maxInputChannels']}ch out={info['maxOutputChannels']}ch "
        f"rate={int(info['defaultSampleRate'])}Hz"
    )


def print_devices():
    """診断用にデバイスを一覧表示する（app.py devices）."""
    p = pyaudio.PyAudio()
    try:
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            print("WASAPI が利用できません。オーディオ ドライバの状態を"
                  "確認してください。", file=sys.stderr)
            return 1

        default_out = wasapi["defaultOutputDevice"]
        default_in = wasapi["defaultInputDevice"]

        print("=" * 70)
        print("出力デバイス (スピーカー/ヘッドフォン)")
        print("=" * 70)
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"]:
                continue
            if d["maxOutputChannels"] > 0 and not d.get("isLoopbackDevice", False):
                print(_fmt(d, "*" if d["index"] == default_out else ""))

        print()
        print("=" * 70)
        print("ループバックデバイス (= PC から流れる音 / 相手の声)")
        print("=" * 70)
        try:
            default_lb = p.get_default_wasapi_loopback()
            default_lb_index = default_lb["index"]
        except Exception:
            default_lb_index = None
        found = False
        for lb in p.get_loopback_device_info_generator():
            found = True
            print(_fmt(lb, "*" if lb["index"] == default_lb_index else ""))
        if not found:
            print("  (見つかりません)")

        print()
        print("=" * 70)
        print("入力デバイス (マイク)")
        print("=" * 70)
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"]:
                continue
            if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
                print(_fmt(d, "*" if d["index"] == default_in else ""))

        print()
        print("* = 既定のデバイス")
        print("既定以外を使う場合: "
              f"{common.cli_hint('record')} --mic <index> --loopback <index>")
        return 0
    finally:
        p.terminate()
