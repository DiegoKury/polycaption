import pyaudio
import speech_recognition as sr


class SetupError(Exception):
    """A user-fixable setup problem (e.g. VB-Cable missing). main.py shows the message
    in a popup instead of dumping a traceback."""


def find_audio_device(fallback=None):
    """Find CABLE Output device index, fall back to config value if not found."""
    names = sr.Microphone.list_microphone_names()
    for i, name in enumerate(names):
        if 'CABLE Output' in name:
            print(f"Found audio device: [{i}] {name}")
            return i
    if fallback is not None:
        print(f"CABLE Output not found, using fallback index {fallback}")
        return fallback
    raise SetupError(
        "VB-Cable not detected.\n\n"
        "Install (or reinstall) VB-Cable from https://vb-audio.com/Cable/ and reboot, "
        "then set your call/app's output to 'CABLE Input'.")


def verify_audio_device(device_index):
    """Quick check that the device can be opened and captures non-silent audio."""
    try:
        with sr.Microphone(device_index=device_index, sample_rate=44100) as source:
            r = sr.Recognizer()
            r.energy_threshold = 100
            r.dynamic_energy_threshold = False
            audio = r.record(source, duration=2)
            raw = audio.get_raw_data()
            energy = sum(abs(b - 128) for b in raw) / len(raw) if raw else 0
            if energy < 1:
                print("WARNING: Audio device opened but no sound detected.")
                print("  -> Make sure output is set to CABLE Input in Windows Sound settings.")
            else:
                print("Audio device OK - sound detected.")
    except Exception as e:
        raise SetupError(f"Could not open audio device {device_index}: {e}")


def find_mic_device():
    """Find a real physical microphone (skips mappers, VB-Cable, and virtual devices)."""
    names = sr.Microphone.list_microphone_names()
    for i, name in enumerate(names):
        low = name.lower()
        skip = ('cable' in low or 'vb-audio' in low or 'virtual' in low
                or 'stereo mix' in low or 'mapper' in low or 'wave mapper' in low
                or 'microsoft sound mapper' in low)
        if not skip:
            print(f"Found microphone: [{i}] {name}")
            return i
    print("No physical microphone found — mic capture disabled")
    return None


def init_audio(fallback=None, verify=True):
    """Find device and optionally verify it. Returns (device_index, recognizer)."""
    idx = find_audio_device(fallback=fallback)
    if verify:
        verify_audio_device(idx)
    return idx, make_recognizer()


def make_recognizer():
    r = sr.Recognizer()
    r.energy_threshold = 100
    r.dynamic_energy_threshold = False
    return r
