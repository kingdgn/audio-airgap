#!/usr/bin/env python3
"""
Audio Airgap Kit

Audible WAV-based file transfer using parallel 4-FSK.

The design mirrors a QR/OTN-style workflow:
  encode  -> split file into indexed frames and write a playable WAV
  decode  -> analyze recorded WAV files, recover frames, list missing indexes
  replay  -> create a short WAV containing only missing frames
  plan    -> estimate transfer time

Frame integrity is protected by CRC32. Whole-file integrity is protected by
SHA256 from the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


MAGIC = b"AOD1"
VERSION = 1
FRAME_MANIFEST = 1
FRAME_DATA = 2

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_CHANNELS = 12
DEFAULT_SYMBOL_MS = 10
DEFAULT_CHUNK_SIZE = 512

PILOT_HZ = 880.0
PILOT_MS = 180
GAP_MS = 160


@dataclass(frozen=True)
class ModemConfig:
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    symbol_ms: int = DEFAULT_SYMBOL_MS
    base_hz: int = 1000

    @property
    def symbol_samples(self) -> int:
        return int(round(self.sample_rate * self.symbol_ms / 1000.0))

    @property
    def bits_per_symbol(self) -> int:
        return self.channels * 2

    @property
    def bytes_per_symbol(self) -> int:
        if self.bits_per_symbol % 8 != 0:
            raise ValueError("channels must be a multiple of 4")
        return self.bits_per_symbol // 8

    @property
    def raw_bitrate(self) -> float:
        return self.bits_per_symbol * 1000.0 / self.symbol_ms

    def tone_plan(self) -> list[list[float]]:
        if self.channels % 4 != 0:
            raise ValueError("channels must be a multiple of 4, for example 8, 12, 16, or 20")
        if self.sample_rate != 48_000:
            raise ValueError("this implementation currently expects 48000 Hz WAV audio")
        if self.symbol_ms not in (10, 20):
            raise ValueError("symbol_ms must be 10 or 20")

        # 10 ms symbols have 100 Hz FFT bins. Frequencies are aligned to bins.
        option_step = 100
        channel_step = 600
        plan: list[list[float]] = []
        for ch in range(self.channels):
            base = self.base_hz + ch * channel_step
            plan.append([base + option_step * value for value in range(4)])

        highest = plan[-1][-1]
        if highest > self.sample_rate / 2 - 2000:
            raise ValueError(f"highest tone {highest} Hz is too close to Nyquist")
        return plan


@dataclass
class ParsedFrame:
    frame_type: int
    file_id: bytes
    index: int
    total: int
    payload: bytes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_id_from_sha256(sha256_hex: str) -> bytes:
    return bytes.fromhex(sha256_hex[:8])


def pack_frame(frame_type: int, file_id: bytes, index: int, total: int, payload: bytes) -> bytes:
    if len(file_id) != 4:
        raise ValueError("file_id must be 4 bytes")
    if len(payload) > 65535:
        raise ValueError("payload too large")

    header = struct.pack(">4sBB4sIIH", MAGIC, VERSION, frame_type, file_id, index, total, len(payload))
    crc = zlib.crc32(header + payload) & 0xFFFFFFFF
    return header + payload + struct.pack(">I", crc)


def parse_frame(data: bytes) -> ParsedFrame | None:
    if len(data) < 24:
        return None

    magic, version, frame_type, file_id, index, total, payload_len = struct.unpack(
        ">4sBB4sIIH", data[:20]
    )
    if magic != MAGIC or version != VERSION:
        return None

    frame_len = 20 + payload_len + 4
    if len(data) < frame_len:
        return None

    frame = data[:frame_len]
    expected_crc = struct.unpack(">I", frame[-4:])[0]
    actual_crc = zlib.crc32(frame[:-4]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        return None

    return ParsedFrame(
        frame_type=frame_type,
        file_id=file_id,
        index=index,
        total=total,
        payload=frame[20:-4],
    )


def parse_index_spec(spec: str) -> list[int]:
    result: set[int] = set()
    for token in re.findall(r"\d+(?:\s*[-~]\s*\d+)?", spec):
        token = re.sub(r"\s+", "", token)
        if "-" in token or "~" in token:
            sep = "-" if "-" in token else "~"
            start_s, end_s = token.split(sep, 1)
            start, end = int(start_s), int(end_s)
            if end < start:
                raise ValueError(f"invalid range {token}")
            result.update(range(start, end + 1))
        else:
            result.add(int(token))
    return sorted(result)


def bytes_to_symbols(data: bytes, cfg: ModemConfig) -> np.ndarray:
    raw = np.frombuffer(data, dtype=np.uint8)
    bits = np.unpackbits(raw, bitorder="big")
    padding = (-len(bits)) % cfg.bits_per_symbol
    if padding:
        bits = np.pad(bits, (0, padding), constant_values=0)
    grouped = bits.reshape((-1, cfg.channels, 2))
    return ((grouped[:, :, 0] << 1) | grouped[:, :, 1]).astype(np.uint8)


def symbols_to_bytes(symbols: np.ndarray) -> bytes:
    bits = np.empty((symbols.shape[0], symbols.shape[1], 2), dtype=np.uint8)
    bits[:, :, 0] = (symbols >> 1) & 1
    bits[:, :, 1] = symbols & 1
    packed = np.packbits(bits.reshape(-1), bitorder="big")
    return packed.tobytes()


def tone_cache(cfg: ModemConfig) -> list[list[np.ndarray]]:
    n = cfg.symbol_samples
    t = np.arange(n, dtype=np.float32) / cfg.sample_rate

    window = np.ones(n, dtype=np.float32)
    fade = max(8, min(n // 8, int(0.0015 * cfg.sample_rate)))
    ramp = np.sin(np.linspace(0, math.pi / 2, fade, dtype=np.float32)) ** 2
    window[:fade] = ramp
    window[-fade:] = ramp[::-1]

    scale = 0.78 / cfg.channels
    cache: list[list[np.ndarray]] = []
    for channel in cfg.tone_plan():
        cache.append(
            [
                (np.sin(2 * np.pi * freq * t).astype(np.float32) * window * scale)
                for freq in channel
            ]
        )
    return cache


def modulate_frame(frame: bytes, cfg: ModemConfig) -> np.ndarray:
    symbols = bytes_to_symbols(frame, cfg)
    cache = tone_cache(cfg)
    n = cfg.symbol_samples
    audio = np.zeros(symbols.shape[0] * n, dtype=np.float32)
    for sym_i, values in enumerate(symbols):
        segment = audio[sym_i * n : (sym_i + 1) * n]
        for ch, value in enumerate(values):
            segment += cache[ch][int(value)]
    return audio


def sine_tone(freq: float, ms: int, sample_rate: int, amplitude: float = 0.45) -> np.ndarray:
    n = int(round(sample_rate * ms / 1000.0))
    t = np.arange(n, dtype=np.float32) / sample_rate
    tone = np.sin(2 * np.pi * freq * t).astype(np.float32) * amplitude
    fade = max(8, min(n // 8, int(0.003 * sample_rate)))
    ramp = np.sin(np.linspace(0, math.pi / 2, fade, dtype=np.float32)) ** 2
    tone[:fade] *= ramp
    tone[-fade:] *= ramp[::-1]
    return tone


def audio_for_frame(frame: bytes, cfg: ModemConfig) -> np.ndarray:
    gap = np.zeros(int(round(cfg.sample_rate * GAP_MS / 1000.0)), dtype=np.float32)
    pilot = sine_tone(PILOT_HZ, PILOT_MS, cfg.sample_rate)
    data = modulate_frame(frame, cfg)
    return np.concatenate([gap, pilot, data, gap])


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def open_wave_writer(path: Path, sample_rate: int) -> wave.Wave_write:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = wave.open(str(path), "wb")
    writer.setnchannels(1)
    writer.setsampwidth(2)
    writer.setframerate(sample_rate)
    return writer


def read_wav_mono(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        sample_rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if sample_width != 2:
        raise ValueError(f"{path} must be a 16-bit PCM WAV")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape((-1, channels)).mean(axis=1)
    return sample_rate, data


def make_manifest(path: Path, cfg: ModemConfig, chunk_size: int) -> dict:
    size = path.stat().st_size
    sha = sha256_file(path)
    total = math.ceil(size / chunk_size)
    return {
        "version": 1,
        "codec": "audio-airgap-parallel-4fsk",
        "filename": path.name,
        "size": size,
        "sha256": sha,
        "file_id": file_id_from_sha256(sha).hex().upper(),
        "chunk_size": chunk_size,
        "total": total,
        "sample_rate": cfg.sample_rate,
        "channels": cfg.channels,
        "symbol_ms": cfg.symbol_ms,
        "raw_bitrate_bps": round(cfg.raw_bitrate, 2),
        "pilot_ms": PILOT_MS,
        "gap_ms": GAP_MS,
    }


def iter_data_frames(path: Path, manifest: dict, selected: Iterable[int] | None = None) -> Iterable[tuple[int, bytes]]:
    selected_set = set(selected) if selected is not None else None
    file_id = bytes.fromhex(manifest["file_id"])
    total = int(manifest["total"])
    chunk_size = int(manifest["chunk_size"])
    with path.open("rb") as fh:
        index = 0
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            if selected_set is None or index in selected_set:
                yield index, pack_frame(FRAME_DATA, file_id, index, total, chunk)
            index += 1


def manifest_frame(manifest: dict) -> bytes:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return pack_frame(FRAME_MANIFEST, bytes.fromhex(manifest["file_id"]), 0, int(manifest["total"]), payload)


def write_transfer_audio(
    source: Path,
    wav_path: Path,
    cfg: ModemConfig,
    chunk_size: int,
    selected: Iterable[int] | None = None,
    repeat_data: int = 1,
    manifest_repeat: int = 3,
    write_frame_wavs: bool = False,
) -> dict:
    manifest = make_manifest(source, cfg, chunk_size)
    selected_list = sorted(set(selected)) if selected is not None else None

    frames_dir = wav_path.parent / "frames"
    if write_frame_wavs:
        frames_dir.mkdir(parents=True, exist_ok=True)

    written_data = 0
    with open_wave_writer(wav_path, cfg.sample_rate) as combined:
        mframe = manifest_frame(manifest)
        for repeat in range(manifest_repeat):
            audio = audio_for_frame(mframe, cfg)
            combined.writeframes(float_to_pcm16(audio))
            if write_frame_wavs:
                with open_wave_writer(frames_dir / f"manifest_{repeat:02d}.wav", cfg.sample_rate) as wf:
                    wf.writeframes(float_to_pcm16(audio))

        for pass_no in range(repeat_data):
            for index, frame in iter_data_frames(source, manifest, selected=selected_list):
                audio = audio_for_frame(frame, cfg)
                combined.writeframes(float_to_pcm16(audio))
                written_data += 1
                if write_frame_wavs:
                    with open_wave_writer(frames_dir / f"frame_{index:06d}_pass_{pass_no:02d}.wav", cfg.sample_rate) as wf:
                        wf.writeframes(float_to_pcm16(audio))

    manifest["written_data_frames"] = written_data
    manifest["selected_indexes"] = selected_list
    manifest["repeat_data"] = repeat_data
    return manifest


def detect_segments(audio: np.ndarray, sample_rate: int, threshold: float | None = None) -> list[tuple[int, int]]:
    window = max(1, int(sample_rate * 0.02))
    usable = len(audio) - (len(audio) % window)
    if usable <= 0:
        return []

    blocks = audio[:usable].reshape((-1, window))
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    if threshold is None:
        high = float(np.percentile(rms, 95))
        threshold = max(0.012, high * 0.18)
    active = rms > threshold

    segments: list[tuple[int, int]] = []
    start: int | None = None
    for i, is_active in enumerate(active):
        if is_active and start is None:
            start = i
        elif not is_active and start is not None:
            segments.append((start * window, i * window))
            start = None
    if start is not None:
        segments.append((start * window, len(audio)))

    merge_gap = int(sample_rate * 0.08)
    min_len = int(sample_rate * 0.25)
    merged: list[tuple[int, int]] = []
    for seg_start, seg_end in segments:
        if seg_end - seg_start < min_len:
            continue
        if merged and seg_start - merged[-1][1] < merge_gap:
            merged[-1] = (merged[-1][0], seg_end)
        else:
            merged.append((seg_start, seg_end))
    return merged


def demodulate_symbols(audio: np.ndarray, cfg: ModemConfig) -> np.ndarray:
    n = cfg.symbol_samples
    symbol_count = len(audio) // n
    if symbol_count <= 0:
        return np.zeros((0, cfg.channels), dtype=np.uint8)

    plan = cfg.tone_plan()
    freqs = np.array(plan, dtype=np.float32)
    bins = np.rint(freqs * n / cfg.sample_rate).astype(int)
    shaped = audio[: symbol_count * n].reshape((symbol_count, n))
    window = np.hanning(n).astype(np.float32)
    spectrum = np.fft.rfft(shaped * window, axis=1)
    magnitude = np.abs(spectrum)

    symbols = np.zeros((symbol_count, cfg.channels), dtype=np.uint8)
    for ch in range(cfg.channels):
        energies = []
        for value in range(4):
            b = bins[ch, value]
            lo = max(0, b - 1)
            hi = min(magnitude.shape[1], b + 2)
            energies.append(magnitude[:, lo:hi].sum(axis=1))
        symbols[:, ch] = np.argmax(np.vstack(energies).T, axis=1).astype(np.uint8)
    return symbols


def try_decode_segment(segment: np.ndarray, cfg: ModemConfig, search_ms: int = 5) -> ParsedFrame | None:
    pilot_samples = int(round(cfg.sample_rate * PILOT_MS / 1000.0))
    search_samples = int(round(cfg.sample_rate * search_ms / 1000.0))
    step = max(1, int(round(cfg.sample_rate * 0.001)))

    offsets = [0]
    for delta in range(step, search_samples + 1, step):
        offsets.extend([delta, -delta])

    for offset in offsets:
        start = pilot_samples + offset
        if start < 0 or start >= len(segment):
            continue
        symbols = demodulate_symbols(segment[start:], cfg)
        if symbols.size == 0:
            continue
        parsed = parse_frame(symbols_to_bytes(symbols))
        if parsed is not None:
            return parsed
    return None


def store_decode_results(out_dir: Path, frames: list[ParsedFrame]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)

    manifests: list[dict] = []
    data_by_file: dict[str, dict[int, bytes]] = {}
    totals: dict[str, int] = {}

    for frame in frames:
        file_key = frame.file_id.hex().upper()
        if frame.frame_type == FRAME_MANIFEST:
            try:
                manifest = json.loads(frame.payload.decode("utf-8"))
                manifests.append(manifest)
                totals[file_key] = int(manifest["total"])
                (out_dir / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception:
                continue
        elif frame.frame_type == FRAME_DATA:
            data_by_file.setdefault(file_key, {})[frame.index] = frame.payload
            totals[file_key] = frame.total
            (chunks_dir / f"{file_key}_{frame.index:06d}.bin").write_bytes(frame.payload)

    manifest = manifests[-1] if manifests else None
    file_key = manifest["file_id"].upper() if manifest else (next(iter(data_by_file.keys()), None))
    received = data_by_file.get(file_key, {}) if file_key else {}
    total = int(manifest["total"]) if manifest else totals.get(file_key, 0)
    missing = [i for i in range(total) if i not in received] if total else []

    recovered = None
    recovered_sha256 = None
    sha_ok = False
    if manifest and total and not missing:
        recovered_dir = out_dir / "recovered"
        recovered_dir.mkdir(exist_ok=True)
        recovered = recovered_dir / manifest["filename"]
        with recovered.open("wb") as fh:
            for i in range(total):
                fh.write(received[i])
        with recovered.open("r+b") as fh:
            fh.truncate(int(manifest["size"]))
        recovered_sha256 = sha256_file(recovered)
        sha_ok = recovered_sha256.lower() == manifest["sha256"].lower()

    state = {
        "valid_frames": len(frames),
        "manifest_seen": manifest is not None,
        "file_id": file_key,
        "total": total,
        "received_count": len(received),
        "missing_count": len(missing),
        "missing": missing,
        "recovered_file": str(recovered) if recovered else None,
        "recovered_sha256": recovered_sha256,
        "sha256_ok": sha_ok,
    }
    (out_dir / "decode-state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "missing.txt").write_text(" ".join(str(i) for i in missing) + "\n", encoding="utf-8")
    return state


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = ModemConfig(sample_rate=args.sample_rate, channels=args.channels, symbol_ms=args.symbol_ms)
    size = args.size
    total = math.ceil(size / args.chunk_size)
    frame_bytes = args.chunk_size + 24
    symbols_per_frame = math.ceil(frame_bytes / cfg.bytes_per_symbol)
    frame_audio_sec = PILOT_MS / 1000 + 2 * GAP_MS / 1000 + symbols_per_frame * args.symbol_ms / 1000
    total_sec = frame_audio_sec * total * args.repeat_data
    print(f"codec: parallel 4-FSK, channels={cfg.channels}, symbol_ms={cfg.symbol_ms}")
    print(f"raw bitrate: {cfg.raw_bitrate:.0f} bps")
    print(f"file size: {size} bytes")
    print(f"chunk size: {args.chunk_size} bytes")
    print(f"data frames: {total}")
    print(f"repeat data: {args.repeat_data}")
    print(f"estimated audio: {total_sec / 60:.1f} minutes ({total_sec:.0f} seconds)")
    return 0


def cmd_encode(args: argparse.Namespace) -> int:
    source = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = ModemConfig(sample_rate=args.sample_rate, channels=args.channels, symbol_ms=args.symbol_ms)
    wav_path = out_dir / args.wav_name
    manifest = write_transfer_audio(
        source,
        wav_path,
        cfg,
        args.chunk_size,
        repeat_data=args.repeat_data,
        manifest_repeat=args.manifest_repeat,
        write_frame_wavs=args.write_frame_wavs,
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "sha256.txt").write_text(f"{manifest['sha256']}  {manifest['filename']}\n", encoding="utf-8")
    print(f"WAV: {wav_path}")
    print(f"Manifest: {out_dir / 'manifest.json'}")
    print(f"SHA256: {manifest['sha256']}")
    print(f"Data frames written: {manifest['written_data_frames']} / {manifest['total']}")
    print(f"Raw bitrate: {manifest['raw_bitrate_bps']} bps")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    source = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = parse_index_spec(args.missing)
    if not selected:
        raise SystemExit("missing list is empty")

    cfg = ModemConfig(sample_rate=args.sample_rate, channels=args.channels, symbol_ms=args.symbol_ms)
    wav_path = out_dir / args.wav_name
    manifest = write_transfer_audio(
        source,
        wav_path,
        cfg,
        args.chunk_size,
        selected=selected,
        repeat_data=args.repeat_data,
        manifest_repeat=args.manifest_repeat,
        write_frame_wavs=args.write_frame_wavs,
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "missing-replay.txt").write_text(" ".join(str(i) for i in selected) + "\n", encoding="utf-8")
    suffix = " ..." if len(selected) > 80 else ""
    print(f"Replay WAV: {wav_path}")
    print(f"Selected frames: {len(selected)}")
    print(f"Indexes: {' '.join(str(i) for i in selected[:80])}{suffix}")
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    cfg = ModemConfig(sample_rate=args.sample_rate, channels=args.channels, symbol_ms=args.symbol_ms)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    valid: list[ParsedFrame] = []
    bad_segments = 0
    total_segments = 0
    for wav_name in args.wav:
        wav_path = Path(wav_name).resolve()
        sample_rate, audio = read_wav_mono(wav_path)
        if sample_rate != cfg.sample_rate:
            raise ValueError(f"{wav_path} sample rate is {sample_rate}, expected {cfg.sample_rate}")
        segments = detect_segments(audio, sample_rate, threshold=args.threshold)
        total_segments += len(segments)
        print(f"{wav_path.name}: detected {len(segments)} segment(s)")
        for start, end in segments:
            parsed = try_decode_segment(audio[start:end], cfg, search_ms=args.search_ms)
            if parsed is None:
                bad_segments += 1
                continue
            valid.append(parsed)

    state = store_decode_results(out_dir, valid)
    state["segments"] = total_segments
    state["bad_segments"] = bad_segments
    (out_dir / "decode-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"valid frames: {state['valid_frames']}")
    print(f"bad segments: {bad_segments}")
    print(f"received: {state['received_count']} / {state['total']}")
    print(f"missing: {state['missing_count']}")
    if state["missing_count"]:
        print(f"missing list: {out_dir / 'missing.txt'}")
    if state["recovered_file"]:
        print(f"recovered: {state['recovered_file']}")
        print(f"sha256 ok: {state['sha256_ok']}")
    return 0 if state["missing_count"] == 0 and (not state["manifest_seen"] or state["sha256_ok"]) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audible audio file transfer with missing-frame replay.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_modem_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
        p.add_argument("--channels", type=int, default=DEFAULT_CHANNELS, help="parallel 4-FSK channels; use 8, 12, 16, or 20")
        p.add_argument("--symbol-ms", type=int, default=DEFAULT_SYMBOL_MS, choices=(10, 20))

    plan = sub.add_parser("plan", help="estimate transfer time")
    add_modem_flags(plan)
    plan.add_argument("--size", type=int, default=600 * 1024)
    plan.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    plan.add_argument("--repeat-data", type=int, default=1)
    plan.set_defaults(func=cmd_plan)

    enc = sub.add_parser("encode", help="encode a file to a WAV transfer")
    add_modem_flags(enc)
    enc.add_argument("--input", required=True)
    enc.add_argument("--out-dir", required=True)
    enc.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    enc.add_argument("--wav-name", default="transmit_all.wav")
    enc.add_argument("--repeat-data", type=int, default=1)
    enc.add_argument("--manifest-repeat", type=int, default=3)
    enc.add_argument("--write-frame-wavs", action="store_true")
    enc.set_defaults(func=cmd_encode)

    dec = sub.add_parser("decode", help="decode one or more received WAV recordings")
    add_modem_flags(dec)
    dec.add_argument("--wav", nargs="+", required=True)
    dec.add_argument("--out-dir", required=True)
    dec.add_argument("--threshold", type=float, default=None)
    dec.add_argument("--search-ms", type=int, default=80)
    dec.set_defaults(func=cmd_decode)

    rep = sub.add_parser("replay", help="encode only missing frame indexes")
    add_modem_flags(rep)
    rep.add_argument("--input", required=True)
    rep.add_argument("--out-dir", required=True)
    rep.add_argument("--missing", required=True, help='indexes like "0 37 40-45"')
    rep.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    rep.add_argument("--wav-name", default="replay_missing.wav")
    rep.add_argument("--repeat-data", type=int, default=2)
    rep.add_argument("--manifest-repeat", type=int, default=2)
    rep.add_argument("--write-frame-wavs", action="store_true")
    rep.set_defaults(func=cmd_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
