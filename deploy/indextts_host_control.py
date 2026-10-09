"""Host-side IndexTTS2 control (no GPU). Starts/stops the speech proxy on :10003.

ArcReel (Docker) reaches this via http://host.docker.internal:10004.
Stdlib only — keep it runnable with the same host Python that has no FastAPI.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent
SPEECH_PROXY = DEPLOY_DIR / "indextts_speech_proxy.py"
PID_FILE = DEPLOY_DIR / "logs" / "indextts_proxy.pid"
LOG_FILE = DEPLOY_DIR / "logs" / "indextts_proxy.log"
CONTROL_PORT = int(os.environ.get("INDEXTTS_CONTROL_PORT", "10004"))
SPEECH_PORT = int(os.environ.get("APP_PORT", os.environ.get("INDEXTTS_SPEECH_PORT", "10003")))
SPEECH_BASE = f"http://127.0.0.1:{SPEECH_PORT}"
PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "indextts_proxy_api_key")

_lock = threading.Lock()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _run_ps(command: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if sys.platform == "win32":
        # CREATE_NO_WINDOW — hide console; avoid windowsHide (not on all Python builds).
        kwargs["creationflags"] = 0x08000000
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        **kwargs,  # type: ignore[arg-type]
    )


def _port_owner(port: int) -> int | None:
    if sys.platform != "win32":
        return None
    ps = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen "
        f"-ErrorAction SilentlyContinue | Select-Object -First 1; "
        f"if ($c) {{ $c.OwningProcess }}"
    )
    try:
        r = _run_ps(ps, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (r.stdout or "").strip()
    if not text:
        return None
    try:
        return int(text.splitlines()[-1].strip())
    except ValueError:
        return None


def _speech_health() -> dict:
    try:
        req = urllib.request.Request(f"{SPEECH_BASE}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                data["running"] = True
                return data
            return {"ok": False, "running": True, "detail": "invalid health payload"}
    except Exception as exc:  # noqa: BLE001 — surface any connect/parse failure
        return {"ok": False, "running": False, "error": str(exc)}


def _kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            kwargs: dict[str, object] = {
                "capture_output": True,
                "text": True,
                "timeout": 15,
                "check": False,
                "creationflags": 0x08000000,
            }
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], **kwargs)  # type: ignore[arg-type]
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _kill_indextts_cli() -> int:
    """Unload GPU workers: terminate residual indextts.cli_v2 Python processes."""
    if sys.platform != "win32":
        return 0
    ps = r"""
$killed = 0
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -match 'python' -and
    $_.CommandLine -and
    ($_.CommandLine -match 'indextts\.cli_v2' -or $_.CommandLine -match 'indextts_speech_proxy')
  } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $script:killed++ } catch {}
  }
Write-Output $killed
"""
    try:
        r = _run_ps(ps, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    text = (r.stdout or "").strip().splitlines()
    if not text:
        return 0
    try:
        return int(text[-1].strip())
    except ValueError:
        return 0


def _read_pid_file() -> int | None:
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _write_pid_file(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("INDEX_TTS_ROOT", r"C:\my\index-tts\index-tts")
    env.setdefault(
        "INDEX_TTS_PYTHON",
        r"C:\my\index-tts\index-tts\.venv\Scripts\python.exe",
    )
    env.setdefault(
        "INDEX_TTS_MODEL_DIR",
        r"C:\my\index-tts\index-tts\checkpoints",
    )
    env.setdefault("INDEX_TTS_DEVICE", "cuda")
    env.setdefault("PROXY_API_KEY", PROXY_API_KEY)
    env.setdefault("APP_PORT", str(SPEECH_PORT))
    env.setdefault(
        "LOCALMINIDRAMA_ROOT",
        r"C:\project\LocalMiniDrama-main\LocalMiniDrama-main\backend-node",
    )
    lmd = env["LOCALMINIDRAMA_ROOT"]
    env.setdefault("GPT_SOVITS_VOICES_FILE", str(Path(lmd) / "data" / "gptsovits" / "voices.json"))
    env.setdefault("GPT_SOVITS_REF_ROOT", str(Path(lmd) / "data" / "gptsovits-refs"))
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    return env


def start_proxy() -> dict:
    with _lock:
        health = _speech_health()
        if health.get("running"):
            return {"ok": True, "started": False, "already_running": True, **health}

        owner = _port_owner(SPEECH_PORT)
        if owner:
            _kill_pid(owner)
            time.sleep(0.8)

        if not SPEECH_PROXY.is_file():
            return {"ok": False, "error": f"missing proxy script: {SPEECH_PROXY}"}

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(LOG_FILE, "a", encoding="utf-8")  # noqa: SIM115 — kept open for child lifetime
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        try:
            proc = subprocess.Popen(
                [sys.executable, str(SPEECH_PROXY)],
                cwd=str(DEPLOY_DIR),
                env=_proxy_env(),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
        except OSError as exc:
            log_fh.close()
            return {"ok": False, "error": f"failed to spawn proxy: {exc}"}

        _write_pid_file(proc.pid)
        # Wait briefly for /health
        deadline = time.time() + 45
        last = {"ok": False, "running": False}
        while time.time() < deadline:
            last = _speech_health()
            if last.get("running"):
                return {
                    "ok": bool(last.get("ok")),
                    "started": True,
                    "pid": proc.pid,
                    **last,
                }
            time.sleep(0.5)

        return {
            "ok": False,
            "started": True,
            "pid": proc.pid,
            "error": "proxy started but health check timed out",
            **last,
        }


def unload_proxy() -> dict:
    with _lock:
        killed = 0
        pid = _read_pid_file()
        if pid and _kill_pid(pid):
            killed += 1
        owner = _port_owner(SPEECH_PORT)
        if owner and owner != pid:
            if _kill_pid(owner):
                killed += 1
        killed += _kill_indextts_cli()
        _clear_pid_file()
        # Confirm port closed
        time.sleep(0.4)
        still = _speech_health()
        return {
            "ok": True,
            "killed": killed,
            "available": bool(still.get("running")),
            "running": bool(still.get("running")),
        }


def status() -> dict:
    health = _speech_health()
    return {
        "control": "indextts_host_control",
        "speech_port": SPEECH_PORT,
        "control_port": CONTROL_PORT,
        "pid_file": str(PID_FILE),
        "pid": _read_pid_file(),
        **health,
    }


def ensure() -> dict:
    started = start_proxy()
    if not started.get("running") and not started.get("ok"):
        return started
    # Optional warm check via CLI through proxy health filesystem fields
    return {
        "ok": bool(started.get("ok")),
        "started": bool(started.get("started")),
        "already_running": bool(started.get("already_running")),
        **{k: v for k, v in started.items() if k not in {"ok", "started", "already_running"}},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[indextts-control] " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health", "/status"):
            _json_response(self, 200, status())
            return
        _json_response(self, 404, {"ok": False, "error": f"unknown path: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/start", "/ensure"):
            result = ensure() if path == "/ensure" else start_proxy()
            _json_response(self, 200 if result.get("ok") or result.get("running") else 503, result)
            return
        if path in ("/stop", "/unload"):
            _json_response(self, 200, unload_proxy())
            return
        _json_response(self, 404, {"ok": False, "error": f"unknown path: {path}"})


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    print(f"IndexTTS host control on 0.0.0.0:{CONTROL_PORT} (speech :{SPEECH_PORT})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("shutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
