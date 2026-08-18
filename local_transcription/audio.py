"""音声デバイスの列挙・録音・着脱監視を、OS ごとの実装へ振り分ける.

Windows は WASAPI (audio_wasapi)、Linux は PulseAudio/PipeWire (audio_pulse)。
録音の同期と WAV 書き出し (record.py)、画面 (gui.py)、診断 (check_devices.py)
はこの層だけを見る。OS 判定をここ 1 箇所に閉じるため。

バックエンドが備えるもの:

    AudioSystem()                          接続を開く（使い終わったら close）
    AudioSystem.list_devices()          -> (ループバック一覧, マイク一覧)
    AudioSystem.resolve_loopback(index) -> device_info
    AudioSystem.resolve_mic(index)      -> device_info
    AudioSystem.open_stream(info, rate, channels, chunk, on_chunk) -> Stream
    AudioSystem.close()
    DeviceWatcher(on_change)               デバイス変更の監視
    print_devices()                        診断用の一覧表示

device_info は PyAudio が返す dict の形を踏襲する
(index / name / defaultSampleRate / maxInputChannels)。バックエンドを足す時に
呼び出し側を書き換えずに済ませるため。

on_chunk(ts, data, overflow) の ts は「そのチャンクの末尾」の時刻
(time.perf_counter 基準)。バッファ遅延の補正はバックエンド側の責任とし、
record.py は届いた時刻をそのまま信じてよいことにする。
"""

import sys

if sys.platform == "win32":
    from . import audio_wasapi as _backend
else:
    from . import audio_pulse as _backend

AudioSystem = _backend.AudioSystem
DeviceWatcher = _backend.DeviceWatcher
print_devices = _backend.print_devices


def open():
    """録音とデバイス列挙のための接続を開く。使い終わったら close() する."""
    return AudioSystem()


def list_devices():
    """(ループバック一覧, マイク一覧) を返す。要素は (表示名, index).

    一覧を見るだけの用途（画面の更新など）はこれで足りる。
    """
    system = open()
    try:
        return system.list_devices()
    finally:
        system.close()
