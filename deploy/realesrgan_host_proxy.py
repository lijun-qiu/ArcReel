"""Host-side Real-ESRGAN video upscale proxy (Windows).

ArcReel (Docker) reaches this via http://host.docker.internal:10005.
Stdlib only — run with host Python; needs ffmpeg on PATH and realesrgan-ncnn-vulkan.exe.

Also watches ``logs/realesrgan_autostart.request`` so ArcReel can request a (re)start
when the proxy was down: the start script's supervisor recreates this process; if this
process is already up, writing the flag is a no-op for callers that only need health.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DEPLOY_DIR = Path(__file__).resolve().parent
CONTROL_PORT = int(os.environ.get("REALESRGAN_PROXY_PORT", "10005"))
DEFAULT_ROOT = Path(r"C:\project\realesrgan-ncnn-vulkan-20220424-windows")
REALESRGAN_ROOT = Path(os.environ.get("REALESRGAN_ROOT", str(DEFAULT_ROOT)))
PROJECTS_ROOT = Path(os.environ.get("PROJECTS_HOST_ROOT", str(DEPLOY_DIR / "projects")))
DEFAULT_MODEL = os.environ.get("REALESRGAN_MODEL", "realesr-animevideov3")
DEFAULT_SCALE = int(os.environ.get("REALESRGAN_SCALE", "2"))
MAX_HEIGHT = int(os.environ.get("REALESRGAN_MAX_HEIGHT", "1080"))
AUTOSTART_FLAG = Path(
    os.environ.get(
        "REALESRGAN_AUTOSTART_FLAG",
        str(DEPLOY_DIR / "logs" / "realesrgan_autostart.request"),
    )
)

_lock = threading.Lock()
_progress_lock = threading.Lock()
_progress: dict[str, object] = {
    "ok": True,
    "busy": False,
    "phase": "idle",
    "percent": 0,
    "message": "",
    "frames_total": 0,
    "frames_done": 0,
    "error": None,
}

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _set_progress(**kwargs: object) -> None:
    with _progress_lock:
        _progress.update(kwargs)


def get_progress() -> dict[str, object]:
    with _progress_lock:
        return dict(_progress)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _exe() -> Path:
    return REALESRGAN_ROOT / "realesrgan-ncnn-vulkan.exe"


def _resolve_under_projects(rel: str) -> Path:
    """Map a projects-relative posix path to an absolute host path; reject traversal."""
    cleaned = rel.replace("\\", "/").lstrip("/")
    if not cleaned or ".." in Path(cleaned).parts:
        raise ValueError(f"invalid relative path: {rel!r}")
    root = PROJECTS_ROOT.resolve()
    target = (root / cleaned).resolve()
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise ValueError(f"path escapes projects root: {rel!r}")
    return target


def _creationflags() -> dict[str, object]:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> None:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=str(cwd) if cwd else None,
        **_creationflags(),  # type: ignore[arg-type]
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[-2000:]
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}\n{err}")


def _probe_has_audio(path: Path) -> bool:
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            **_creationflags(),  # type: ignore[arg-type]
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool((r.stdout or "").strip())


def _probe_frame_count(path: Path) -> int:
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            **_creationflags(),  # type: ignore[arg-type]
        )
        text = (r.stdout or "").strip().splitlines()
        if text and text[-1].isdigit():
            return int(text[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0


def _count_pngs(directory: Path) -> int:
    try:
        return sum(1 for p in directory.iterdir() if p.suffix.lower() == ".png")
    except OSError:
        return 0


def _run_realesrgan(
    *,
    exe: Path,
    frames_in: Path,
    frames_out: Path,
    model: str,
    scale: int,
    frames_total: int,
) -> None:
    """Run realesrgan and stream progress into ``_progress``."""
    cmd = [
        str(exe),
        "-i",
        str(frames_in),
        "-o",
        str(frames_out),
        "-n",
        model,
        "-s",
        str(scale),
        "-f",
        "png",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REALESRGAN_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **_creationflags(),  # type: ignore[arg-type]
    )
    assert proc.stdout is not None
    last_pct = 5.0
    stop_poll = threading.Event()

    def _poll_frames() -> None:
        nonlocal last_pct
        while not stop_poll.wait(1.0):
            done = _count_pngs(frames_out)
            if frames_total > 0 and done > 0:
                # extract 0-5%, upscale 5-90%
                pct = 5.0 + 85.0 * min(done, frames_total) / frames_total
                # Always publish frame counts; only raise percent (never go backwards).
                if pct > last_pct:
                    last_pct = pct
                _set_progress(
                    phase="upscale",
                    percent=round(last_pct, 1),
                    frames_done=done,
                    frames_total=frames_total,
                    message=f"upscaling frames {done}/{frames_total}",
                )

    poller = threading.Thread(target=_poll_frames, name="realesrgan-frame-poll", daemon=True)
    poller.start()
    try:
        for line in proc.stdout:
            m = _PCT_RE.search(line)
            if m:
                raw = float(m.group(1))
                # Per-tile tool % is noisy; only nudge when it raises overall estimate.
                # Prefer frame-count progress from the poller for long folder jobs.
                done = _count_pngs(frames_out)
                if frames_total > 0 and done > 0:
                    frame_pct = 5.0 + 85.0 * min(done, frames_total) / frames_total
                else:
                    frame_pct = 5.0 + 85.0 * max(0.0, min(raw, 100.0)) / 100.0
                if frame_pct > last_pct:
                    last_pct = frame_pct
                    _set_progress(
                        phase="upscale",
                        percent=round(last_pct, 1),
                        frames_done=done,
                        frames_total=frames_total,
                        message=f"upscaling frames {done}/{frames_total}" if done else (line.strip()[:200] or f"upscaling {raw:.1f}%"),
                    )
        rc = proc.wait(timeout=7200)
    finally:
        stop_poll.set()
        poller.join(timeout=2)
        if proc.poll() is None:
            proc.kill()
    if rc != 0:
        raise RuntimeError(f"realesrgan failed ({rc}): {' '.join(cmd)}")


def _upscale_video(
    *,
    input_path: Path,
    output_path: Path,
    scale: int,
    model: str,
) -> dict:
    if not input_path.is_file():
        raise FileNotFoundError(f"input missing: {input_path}")
    exe = _exe()
    if not exe.is_file():
        raise FileNotFoundError(f"realesrgan exe missing: {exe}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    frames_total = _probe_frame_count(input_path)
    _set_progress(
        busy=True,
        phase="extract",
        percent=1,
        message="extracting frames",
        frames_total=frames_total,
        frames_done=0,
        error=None,
    )

    work = Path(tempfile.mkdtemp(prefix="realesrgan_", dir=str(DEPLOY_DIR / "logs")))
    frames_in = work / "in"
    frames_out = work / "out"
    frames_in.mkdir(parents=True)
    frames_out.mkdir(parents=True)
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                str(frames_in / "%08d.png"),
            ],
            timeout=1800,
        )
        extracted = _count_pngs(frames_in)
        if extracted > 0:
            frames_total = extracted
        _set_progress(
            phase="upscale",
            percent=5,
            frames_total=frames_total,
            frames_done=0,
            message=f"extracted {extracted} frames",
        )

        _run_realesrgan(
            exe=exe,
            frames_in=frames_in,
            frames_out=frames_out,
            model=model,
            scale=scale,
            frames_total=frames_total or extracted,
        )

        _set_progress(phase="encode", percent=92, message="encoding video")

        fps = "24"
        try:
            pr = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=r_frame_rate",
                    "-of",
                    "default=nw=1:nk=1",
                    str(input_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                **_creationflags(),  # type: ignore[arg-type]
            )
            rate = (pr.stdout or "").strip()
            if rate and "/" in rate:
                num, den = rate.split("/", 1)
                if float(den) != 0:
                    fps = f"{float(num) / float(den):.6g}"
            elif rate:
                fps = rate
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

        # Keep a real video extension so ffmpeg can pick a muxer
        # (`.mp4.partial` alone fails with "Unable to choose an output format").
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
        encode_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            fps,
            "-i",
            str(frames_out / "%08d.png"),
        ]
        has_audio = _probe_has_audio(input_path)
        if has_audio:
            encode_cmd.extend(["-i", str(input_path)])
        encode_cmd.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
            ]
        )
        if MAX_HEIGHT > 0:
            encode_cmd.extend(["-vf", f"scale=-2:{MAX_HEIGHT}:flags=lanczos"])
        if has_audio:
            encode_cmd.extend(["-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-shortest"])
        encode_cmd.extend(["-f", "mp4", str(tmp_out)])
        _run(encode_cmd, timeout=1800)
        os.replace(tmp_out, output_path)
        _set_progress(
            busy=False,
            phase="done",
            percent=100,
            message="done",
            frames_done=frames_total,
            frames_total=frames_total,
            error=None,
        )
        return {
            "ok": True,
            "input": str(input_path),
            "output": str(output_path),
            "scale": scale,
            "model": model,
            "fps": fps,
            "has_audio": has_audio,
            "frames": frames_total,
        }
    except Exception as exc:
        _set_progress(busy=False, phase="error", error=str(exc), message=str(exc))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[realesrgan] " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/health", "/status"):
            exe = _exe()
            payload = {
                "ok": True,
                "running": True,
                "port": CONTROL_PORT,
                "realesrgan_root": str(REALESRGAN_ROOT),
                "exe_exists": exe.is_file(),
                "projects_root": str(PROJECTS_ROOT),
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "model": DEFAULT_MODEL,
                "scale": DEFAULT_SCALE,
            }
            payload.update(get_progress())
            _json_response(self, 200, payload)
            return
        if path == "/progress":
            _json_response(self, 200, {"ok": True, **get_progress()})
            return
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/ensure":
            # Already running if this handler answers.
            _json_response(self, 200, {"ok": True, "running": True, "started": False})
            return
        if path != "/upscale":
            _json_response(self, 404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"ok": False, "error": "body must be object"})
            return

        input_rel = payload.get("input_rel")
        output_rel = payload.get("output_rel")
        input_path_raw = payload.get("input_path")
        output_path_raw = payload.get("output_path")
        try:
            if input_rel and output_rel:
                input_path = _resolve_under_projects(str(input_rel))
                output_path = _resolve_under_projects(str(output_rel))
            elif input_path_raw and output_path_raw:
                input_path = Path(str(input_path_raw))
                output_path = Path(str(output_path_raw))
            else:
                raise ValueError("need input_rel/output_rel or input_path/output_path")
            scale = int(payload.get("scale") or DEFAULT_SCALE)
            if scale not in (2, 3, 4):
                raise ValueError("scale must be 2, 3, or 4")
            model = str(payload.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        except (TypeError, ValueError) as exc:
            _json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        acquired = _lock.acquire(blocking=False)
        if not acquired:
            _json_response(
                self,
                409,
                {"ok": False, "error": "upscale already running", **get_progress()},
            )
            return
        try:
            result = _upscale_video(
                input_path=input_path,
                output_path=output_path,
                scale=scale,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 — surface to API caller
            _json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": str(exc),
                    "detail": traceback.format_exc()[-1500:],
                    **get_progress(),
                },
            )
            return
        finally:
            _lock.release()
        _json_response(self, 200, result)


def main() -> None:
    (DEPLOY_DIR / "logs").mkdir(parents=True, exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    # Clear stale ensure request so supervisor does not loop-restart us.
    try:
        if AUTOSTART_FLAG.is_file():
            AUTOSTART_FLAG.unlink()
    except OSError:
        pass
    server = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    print(
        f"RealESRGAN proxy on :{CONTROL_PORT} root={REALESRGAN_ROOT} projects={PROJECTS_ROOT}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
