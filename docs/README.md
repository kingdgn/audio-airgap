# GitHub Pages Receiver

This folder is a static mobile receiver site.

Deploy options:

1. Copy `index.html` to a GitHub repository.
2. Enable GitHub Pages for that repository.
3. Open the HTTPS Pages URL on the phone.
4. Tap `Start`, grant microphone permission, then play the WAV from the sender PC.

The receiver settings must match the sender settings:

- `Channels`: same as `--channels`
- `Symbol ms`: same as `--symbol-ms`

Recommended first test:

```powershell
windows\audio_airgap\audio_airgap.exe encode --input small.txt --out-dir audio-out-small --chunk-size 64 --channels 12 --symbol-ms 10
```

After the phone reports missing indexes, generate a targeted replay WAV:

```powershell
windows\audio_airgap\audio_airgap.exe replay --input small.txt --out-dir audio-replay-001 --chunk-size 64 --channels 12 --symbol-ms 10 --missing "0 3 7-9"
```

