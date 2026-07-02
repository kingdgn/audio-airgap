# Windows Precompiled Build

Use the onedir executable because it does not need to extract bundled DLLs to a temporary directory.

Main executable:

```text
windows\audio_airgap\audio_airgap.exe
```

Examples:

```powershell
windows\audio_airgap\audio_airgap.exe plan --size 614400 --chunk-size 512 --channels 12 --symbol-ms 10

windows\audio_airgap\audio_airgap.exe encode `
  --input "D:\path\file.bin" `
  --out-dir "D:\audio-out-file" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10

windows\audio_airgap\audio_airgap.exe replay `
  --input "D:\path\file.bin" `
  --out-dir "D:\audio-replay-001" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10 `
  --missing "0 37 40-45"
```

The `windows\audio_web\audio_web.exe` executable is only for local desktop testing. For phone use, prefer the static GitHub Pages receiver in `site\index.html`.
