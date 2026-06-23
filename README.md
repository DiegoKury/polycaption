# Transcript Tool

Real-time **bilingual (EN/ES) conversation transcriber**. Captures audio, transcribes
each phrase with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), translates
it locally with [argos-translate](https://github.com/argosopentech/argos-translate), and
shows both languages in an overlay that is **invisible to screen capture / screen share**.

## How it works

- **Speaker audio** is captured via VB-Cable's `CABLE Output` device (set your call/app
  output to `CABLE Input`).
- **Your microphone** is captured too (set `capture_mic: false` to disable).
- Each phrase is transcribed in the spoken language, then the other language is filled in
  by argos. Both lines render as blue text, updating live as you speak.

## Setup

1. Install [VB-Cable](https://vb-audio.com/Cable/) and reboot.
2. `pip install -r requirements.txt`
3. Copy `config.example.json` to `config.json` and adjust it to taste (see
   [Config](#config-configjson) below). `config.json` is gitignored, so your local
   tweaks (e.g. `audio_device_index`) won't get committed. If you skip this step,
   `main.py` creates a default `config.json` for you on first run.
4. `python main.py`
   - First run will download the Whisper `small` model and the EN↔ES argos packages
     automatically, so it will take a bit longer than usual.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Shift+↑/↓` | Scroll transcript history |
| `Ctrl+Shift+←/→` | Move (tap) / resize width (hold) |
| `Ctrl+Shift+ -/=` | Opacity down / up |
| `Ctrl+Shift+Q` | Quit |

## Config (`config.json`)

There's no need to create this file by hand — `config.example.json` has every key with its
default value, and `main.py` will write a fresh `config.json` from those defaults on first
run if one doesn't exist yet. Edit the values below to taste.

| Key | Default | Notes |
|---|---|---|
| `whisper_model` | `small` | `base` faster / `medium` more accurate |
| `capture_mic` | `true` | also transcribe your own mic |
| `speaker_max_seconds` | `180` | hard cap on a single speaker phrase before it's force-committed |
| `speaker_pause_threshold` | `1.0` | seconds of speaker silence that ends a phrase |
| `mic_pause_threshold` | `0.6` | seconds of mic silence that ends a phrase |
| `mic_max_seconds` | `60` | hard cap on a single mic phrase before it's force-committed |
| `live_transcription` | `true` | show an updating partial line while you're still speaking |
| `partial_interval` | `1.2` | seconds between live partial-transcript updates |
| `commit_after_seconds` | `18` | for long phrases, transcribe in chunks of this length instead of waiting for silence |
| `beam_size` | `1` | greedy = lowest latency; raise for accuracy |
| `audio_device_index` | `null` | fallback device index if `CABLE Output` isn't found |
