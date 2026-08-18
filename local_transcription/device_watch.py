"""音声デバイスの着脱・既定変更を Windows Core Audio のイベントで監視する.

ポーリングせず通知ベースにするため、変化が無い間のコストはほぼ無い
（実測: list_devices() のポーリングは 1 回あたり 60〜75ms かかる）。

ここで使う COM インタフェース定義（IMMNotificationClient /
IMMDeviceEnumerator）は Windows SDK の mmdeviceapi.h をそのまま写した
もの。vtable はメソッドの並び順で決まるため、呼ばない関数も含めて
本物と同じ順番で定義する必要がある（順番を崩すと落ちる）。
"""

import sys
import threading
from ctypes import HRESULT, POINTER, Structure
from ctypes.wintypes import DWORD, LPCWSTR

# MTA にしないとコールバックを受け取るのにメッセージポンプが要る。
# comtypes は最初に CoInitialize する時にだけこの値を見るので、
# import より前に置く（このプロセスで COM を使うのはここだけの想定）。
sys.coinit_flags = 0  # COINIT_MULTITHREADED

import comtypes  # noqa: E402
from comtypes import COMMETHOD, COMObject, GUID, IUnknown  # noqa: E402

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


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
        self._keep_alive.wait()  # プロセス終了まで待つだけ（daemon なので join 不要）
