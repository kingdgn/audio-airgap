# Audio Airgap

Audio Airgap 是一个“音频版 OTN”离线文件传输工具。发送端把文件切成带序号的数据帧，并调制成可播放的 WAV 音频；接收端在手机浏览器中打开静态网页，点击 **Start** 授权麦克风后持续监听音频、实时解码片段、显示缺失片段，并在收齐后通过 SHA256 校验并下载恢复出的文件。

手机接收页面：

```text
https://kingdgn.github.io/audio-airgap/
```

## 项目内容

- `docs/index.html`：可部署到 GitHub Pages 的手机端静态接收页面。
- `windows/audio_airgap/audio_airgap.exe`：Windows 预编译发送端/补播端命令行工具。
- `src/audio_airgap.py`：Python 源码，包含编码、解码、缺片补播和时长估算。
- `src/audio_web.py`：可选的本地桌面网页分析器。

Windows 预编译版本使用 PyInstaller `onedir` 方式打包。请保持整个 `windows/audio_airgap` 目录完整，不要只单独复制 `audio_airgap.exe`，否则依赖文件会缺失。

## 工作原理

- 调制方式：可听频段 parallel 4-FSK。
- 默认参数：48 kHz 音频、12 组并行频率、10 ms 符号长度。
- 单帧校验：每个音频帧带 CRC32。
- 整文件校验：manifest 中记录 SHA256。
- 缺片恢复：手机端显示缺失 index，发送端只生成这些缺片的补播 WAV。

## 参数调优简表

| 参数 | 含义 | 调整建议 |
|---|---|---|
| `--channels` | 并行频率组数量，每组 4-FSK 承载 2 bit | 增大可缩短时间，如 12 -> 16 -> 20；但更依赖扬声器/麦克风频响 |
| `--chunk-size` | 每个数据帧携带的文件字节数 | 512 稳妥；1024 可减少帧数和总时长，但坏一帧时补播更长 |
| `--symbol-ms` | 每个符号持续时间 | 10ms 更快，20ms 更稳但更慢；默认建议保持 10ms |
| `--repeat-data` | 全量数据重复播放次数 | 默认 1；不要轻易加倍，建议先传一遍再按缺片补播 |

推荐先用 `--chunk-size 512 --channels 12 --symbol-ms 10` 跑通；若缺片很少，再尝试 `--chunk-size 1024 --channels 16 --symbol-ms 10` 来缩短音频时间。
## 快速开始

### 1. Windows 端生成发送音频

```powershell
windows\audio_airgap\audio_airgap.exe encode `
  --input "D:\path\file.bin" `
  --out-dir "D:\audio-out-file" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10
```

输出目录中会生成：

- `transmit_all.wav`：需要播放的完整音频。
- `manifest.json`：文件名、大小、SHA256、分片数和调制参数。
- `sha256.txt`：人工核对用摘要。

### 2. 手机端接收

1. 手机打开 GitHub Pages 接收页面。
2. `Channels` 和 `Symbol ms` 必须与发送端命令一致。
3. 点击 **Start**，允许浏览器使用麦克风。
4. 在发送端电脑播放 `transmit_all.wav`。
5. 手机页面会持续显示已接收数量、缺失片段和下载按钮。

### 3. 缺片补播

如果手机页面显示缺失 index，把缺片列表带回发送端，例如：

```text
0 37 40-45
```

然后生成只包含缺片的补播音频：

```powershell
windows\audio_airgap\audio_airgap.exe replay `
  --input "D:\path\file.bin" `
  --out-dir "D:\audio-replay-001" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10 `
  --missing "0 37 40-45"
```

播放 `audio-replay-001\replay_missing.wav`，手机页面保持监听即可。接收端会自动去重并补齐缺片。

## 估算传输时间

```powershell
windows\audio_airgap\audio_airgap.exe plan --size 614400 --chunk-size 512 --channels 12 --symbol-ms 10
```

600 KiB 文件的典型估算：

| 并行通道数 | 估算时间 |
|---:|---:|
| 12 | 约 46 分钟 |
| 16 | 约 37 分钟 |
| 20 | 约 31 分钟 |

通道数越多速度越快，但对扬声器、麦克风和环境噪声的要求也更高。第一次现场测试建议先用 12 通道。

## 推荐测试流程

先用小文件确认环境稳定：

```powershell
windows\audio_airgap\audio_airgap.exe encode `
  --input "small.txt" `
  --out-dir "audio-out-small" `
  --chunk-size 64 `
  --channels 12 `
  --symbol-ms 10
```

小文件能稳定恢复后，再传目标文件：

```powershell
windows\audio_airgap\audio_airgap.exe encode `
  --input "file.bin" `
  --out-dir "audio-out-file" `
  --chunk-size 512 `
  --channels 12 `
  --symbol-ms 10
```

如果现场噪声较大、缺片较多，可以尝试：

- 降低 `--channels`，例如从 16 降到 12。
- 增大发送端音量，但不要让扬声器破音。
- 让手机靠近扬声器，并减少环境噪声。
- 使用缺片补播，不要整段重传。

## 本地桌面分析器

主要接收方式是手机打开 `docs/index.html`。如果需要在电脑上本地分析录音 WAV，也可以使用 Python 版本：

```powershell
python src\audio_web.py --host 127.0.0.1 --port 8765
```

然后打开：

```text
http://127.0.0.1:8765/
```

## 注意事项

- 手机浏览器调用麦克风通常要求 HTTPS，GitHub Pages 满足这个条件。
- 发送端和接收端的 `Channels`、`Symbol ms` 必须一致。
- 第一次测试建议使用较小文件和较低通道数，确认稳定后再传 600 KiB 级文件。
- 收齐后必须以页面显示的 SHA256 校验结果为准。
