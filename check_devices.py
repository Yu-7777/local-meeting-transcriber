"""WASAPI のオーディオデバイスを一覧表示する診断ツール.

録音に渡すデバイス index をここで確認する。
"""

import sys

import pyaudiowpatch as pyaudio

import common


def fmt(info, mark=""):
    return (
        f"  [{info['index']:>3}] {mark:<2}{info['name']}\n"
        f"        in={info['maxInputChannels']}ch out={info['maxOutputChannels']}ch "
        f"rate={int(info['defaultSampleRate'])}Hz"
    )


def main():
    common.use_utf8_stdout()

    p = pyaudio.PyAudio()
    try:
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            print("WASAPI が利用できません。Windows 環境で実行してください。", file=sys.stderr)
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
                print(fmt(d, "*" if d["index"] == default_out else ""))

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
            print(fmt(lb, "*" if lb["index"] == default_lb_index else ""))
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
                print(fmt(d, "*" if d["index"] == default_in else ""))

        print()
        print("* = 既定のデバイス")
        print("既定以外を使う場合: "
              f"{common.cli_hint('record')} --mic <index> --loopback <index>")
        return 0
    finally:
        p.terminate()


if __name__ == "__main__":
    sys.exit(main())
