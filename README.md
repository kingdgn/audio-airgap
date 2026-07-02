# Audio Airgap

Audio Airgap is an audio version of an OTN-style offline transfer tool. The sender encodes a file into audible WAV audio; the receiver opens a static web page on a phone, taps **Start**, grants microphone permission, continuously decodes audio frames, reports missing frame indexes, and downloads the recovered file after SHA256 verification.

Expected public receiver URL after GitHub Pages is enabled:

```text
https://kingdgn.github.io/audio-airgap/
```

## What It Contains

- `docs/index.html` - static mobile receiver for GitHub Pages.
- `windows/audio_airgap/audio_airgap.exe` - precompiled Windows sender/replay CLI.
- `src/audio_airgap.py` - Python source for encode/decode/replay.
- `src/audio_web.py` - optional local desktop analyzer.

The precompiled Windows build is a PyInstaller `onedir` build. Keep the whole `windows/audio_airgap` folder together; run `audio_airgap.exe` inside it.

## How It Works

- Modulation: audible parallel 4-FSK.
- Default parameters: 48 kHz audio, 12 parallel tone groups, 10 ms symbols.
- Frame integrity: CRC32 per audio frame.
- File integrity: SHA256 from the manifest.
- Missing recovery: receiver reports missing indexes; sender generates a replay WAV containing only those frames.

## Quick Start

### 1. Encode a File on Windows

```powershell
windows\audio_airgap\audio_airgap.exe encode `
  --input "D:\path\file.bin" `
  --out-dir "D:\audio-out-file" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10
```

This creates:

- `transmit_all.wav`
- `manifest.json`
- `sha256.txt`

### 2. Receive on a Phone

1. Open the GitHub Pages receiver on the phone.
2. Set `Channels` and `Symbol ms` to match the sender command.
3. Tap **Start** and grant microphone permission.
4. Play `transmit_all.wav` from the sender machine.
5. Wait for the receiver to show progress, missing indexes, or a download button.

### 3. Replay Missing Frames

If the phone reports missing indexes, copy the missing list back to the sender:

```powershell
windows\audio_airgap\audio_airgap.exe replay `
  --input "D:\path\file.bin" `
  --out-dir "D:\audio-replay-001" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10 `
  --missing "0 37 40-45"
```

Play `replay_missing.wav` while the phone receiver is still listening.

## Estimate Transfer Time

```powershell
windows\audio_airgap\audio_airgap.exe plan --size 614400 --chunk-size 512 --channels 12 --symbol-ms 10
```

Typical estimates for a 600 KiB file:

| Channels | Estimated time |
|---:|---:|
| 12 | about 46 minutes |
| 16 | about 37 minutes |
| 20 | about 31 minutes |

Higher channel counts are faster but require better speaker/microphone frequency response.

## Recommended Test Flow

Start small before sending a 600 KiB file:

```powershell
windows\audio_airgap\audio_airgap.exe encode `
  --input "small.txt" `
  --out-dir "audio-out-small" `
  --chunk-size 64 `
  --channels 12 `
  --symbol-ms 10
```

After that works reliably, move to:

```powershell
windows\audio_airgap\audio_airgap.exe encode `
  --input "file.bin" `
  --out-dir "audio-out-file" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10
```

## Local Desktop Analyzer

The main phone path is `docs/index.html`, but a local desktop analyzer is also included in source form:

```powershell
python src\audio_web.py --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765/
```

## Notes

- The receiver page must be served over HTTPS on phones for microphone permission. GitHub Pages satisfies this.
- Keep sender and receiver parameters matched.
- Use visible/audible playback volume first, then tune volume downward only after reliability is confirmed.
- If decoding stalls, use fewer channels, lower ambient noise, or place the phone closer to the speaker.
