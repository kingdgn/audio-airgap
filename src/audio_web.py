#!/usr/bin/env python3
"""
Local web analyzer for Audio Airgap Kit.

Run:
  python audio_web.py --host 127.0.0.1 --port 8765

Then open:
  http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import cgi
import html
import json
import re
import shutil
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import audio_airgap


ROOT = Path(__file__).resolve().parent
SESSIONS = ROOT / "sessions"


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Audio Airgap Analyzer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #1d2528;
      --muted: #667275;
      --line: #d7ddde;
      --accent: #0f766e;
      --bad: #b42318;
      --ok: #067647;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    header {
      padding: 20px 24px 14px;
      border-bottom: 1px solid var(--line);
      background: #fff;
    }
    h1 { margin: 0; font-size: 22px; font-weight: 650; }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 420px) 1fr;
      gap: 16px;
      padding: 16px 24px 24px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    h2 { margin: 0 0 12px; font-size: 15px; }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin: 12px 0 6px;
    }
    input, button, textarea {
      font: inherit;
      width: 100%;
    }
    input[type="number"], input[type="text"], input[type="file"], textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      background: #fff;
      color: var(--ink);
    }
    textarea {
      min-height: 120px;
      resize: vertical;
      font-family: Consolas, ui-monospace, monospace;
      font-size: 12px;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    button {
      margin-top: 14px;
      border: 0;
      border-radius: 6px;
      padding: 10px 12px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-weight: 650;
    }
    button:disabled { opacity: .55; cursor: wait; }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfb;
    }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 6px; font-size: 22px; }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    pre {
      margin: 12px 0 0;
      padding: 12px;
      border-radius: 8px;
      background: #111827;
      color: #e5e7eb;
      overflow: auto;
      min-height: 120px;
      font-size: 12px;
    }
    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    .actions a {
      color: var(--accent);
      font-weight: 650;
      text-decoration: none;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; padding: 12px; }
      .stats { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <h1>Audio Airgap Analyzer</h1>
  </header>
  <main>
    <section>
      <h2>Decode</h2>
      <form id="decodeForm">
        <label>WAV recordings</label>
        <input name="wav" type="file" accept=".wav,audio/wav" multiple required>
        <div class="row">
          <div>
            <label>Channels</label>
            <input name="channels" type="number" value="12" min="4" max="20" step="4">
          </div>
          <div>
            <label>Symbol ms</label>
            <input name="symbol_ms" type="number" value="10" min="10" max="20" step="10">
          </div>
        </div>
        <div class="row">
          <div>
            <label>Search ms</label>
            <input name="search_ms" type="number" value="80" min="0" max="200">
          </div>
          <div>
            <label>Threshold</label>
            <input name="threshold" type="text" placeholder="auto">
          </div>
        </div>
        <button id="decodeButton" type="submit">Analyze WAV</button>
      </form>
      <label>Missing indexes</label>
      <textarea id="missingBox" readonly></textarea>
    </section>
    <section>
      <h2>Status</h2>
      <div class="stats">
        <div class="metric"><span>Frames</span><strong id="frames">0</strong></div>
        <div class="metric"><span>Received</span><strong id="received">0/0</strong></div>
        <div class="metric"><span>Missing</span><strong id="missing">0</strong></div>
        <div class="metric"><span>SHA256</span><strong id="sha">-</strong></div>
      </div>
      <div class="actions" id="actions"></div>
      <pre id="log">Ready.</pre>
    </section>
  </main>
  <script>
    const form = document.getElementById("decodeForm");
    const button = document.getElementById("decodeButton");
    const log = document.getElementById("log");
    const missingBox = document.getElementById("missingBox");
    const actions = document.getElementById("actions");

    function setText(id, text, className) {
      const el = document.getElementById(id);
      el.textContent = text;
      el.className = className || "";
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      log.textContent = "Analyzing...";
      actions.innerHTML = "";
      try {
        const data = new FormData(form);
        const response = await fetch("/decode", { method: "POST", body: data });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.error || "decode failed");
        }
        setText("frames", String(result.valid_frames));
        setText("received", `${result.received_count}/${result.total}`);
        setText("missing", String(result.missing_count), result.missing_count ? "bad" : "ok");
        setText("sha", result.sha256_ok ? "OK" : (result.manifest_seen ? "Pending" : "-"), result.sha256_ok ? "ok" : "");
        missingBox.value = (result.missing || []).join(" ");
        log.textContent = JSON.stringify(result, null, 2);
        if (result.recovered_file) {
          const link = document.createElement("a");
          link.href = `/download?session=${encodeURIComponent(result.session)}`;
          link.textContent = "Download recovered file";
          actions.appendChild(link);
        }
      } catch (error) {
        log.textContent = String(error);
      } finally {
        button.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AudioAirgapWeb/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/download":
            query = parse_qs(parsed.query)
            session = query.get("session", [""])[0]
            if not re_match_session(session):
                self.send_error(404)
                return
            session_dir = SESSIONS / session
            state_path = session_dir / "decoded" / "decode-state.json"
            if not state_path.exists():
                self.send_error(404)
                return
            state = json.loads(state_path.read_text(encoding="utf-8"))
            recovered = state.get("recovered_file")
            if not recovered or not Path(recovered).exists():
                self.send_error(404)
                return
            path = Path(recovered)
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f"attachment; filename={html.escape(path.name)}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/decode":
            self.send_error(404)
            return
        try:
            result = self.handle_decode()
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})
            return
        self.send_json(200, result)

    def handle_decode(self) -> dict:
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            raise ValueError("expected multipart/form-data")

        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": ctype,
        })

        channels = int(get_form_value(form, "channels", "12"))
        symbol_ms = int(get_form_value(form, "symbol_ms", "10"))
        search_ms = int(get_form_value(form, "search_ms", "80"))
        threshold_raw = get_form_value(form, "threshold", "").strip()
        threshold = float(threshold_raw) if threshold_raw else None
        cfg = audio_airgap.ModemConfig(channels=channels, symbol_ms=symbol_ms)

        session = uuid.uuid4().hex
        session_dir = SESSIONS / session
        upload_dir = session_dir / "uploads"
        decode_dir = session_dir / "decoded"
        upload_dir.mkdir(parents=True, exist_ok=True)

        wav_fields = form["wav"] if "wav" in form else []
        if not isinstance(wav_fields, list):
            wav_fields = [wav_fields]
        if not wav_fields:
            raise ValueError("no WAV files uploaded")

        valid: list[audio_airgap.ParsedFrame] = []
        total_segments = 0
        bad_segments = 0
        uploaded = []

        for item in wav_fields:
            filename = Path(item.filename or "upload.wav").name
            wav_path = upload_dir / filename
            with wav_path.open("wb") as out:
                shutil.copyfileobj(item.file, out)
            uploaded.append(str(wav_path))
            sample_rate, audio = audio_airgap.read_wav_mono(wav_path)
            if sample_rate != cfg.sample_rate:
                raise ValueError(f"{filename}: sample rate {sample_rate}, expected {cfg.sample_rate}")
            segments = audio_airgap.detect_segments(audio, sample_rate, threshold=threshold)
            total_segments += len(segments)
            for start, end in segments:
                parsed = audio_airgap.try_decode_segment(audio[start:end], cfg, search_ms=search_ms)
                if parsed is None:
                    bad_segments += 1
                else:
                    valid.append(parsed)

        state = audio_airgap.store_decode_results(decode_dir, valid)
        state["session"] = session
        state["segments"] = total_segments
        state["bad_segments"] = bad_segments
        state["uploaded"] = uploaded
        (decode_dir / "decode-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return state


def get_form_value(form: cgi.FieldStorage, key: str, default: str) -> str:
    if key not in form:
        return default
    value = form.getvalue(key)
    if isinstance(value, list):
        return str(value[0]) if value else default
    return str(value)


def re_match_session(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", value or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Local web analyzer for Audio Airgap Kit.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    SESSIONS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Audio Airgap Analyzer: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
