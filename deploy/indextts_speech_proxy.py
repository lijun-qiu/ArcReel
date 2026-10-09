"""OpenAI-compatible /v1/audio/speech shim for local IndexTTS2.

Runs on the host (GPU). ArcReel in Docker calls host.docker.internal:10003.
Mirrors LocalMiniDrama indexTtsService.js: serial CLI synth + gsv voice catalog.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("indextts_speech_proxy")

INDEX_TTS_ROOT = Path(os.environ.get("INDEX_TTS_ROOT", r"C:\my\index-tts\index-tts"))
INDEX_TTS_MODEL_DIR = Path(
    os.environ.get("INDEX_TTS_MODEL_DIR", str(INDEX_TTS_ROOT / "checkpoints"))
)
INDEX_TTS_PYTHON = Path(
    os.environ.get(
        "INDEX_TTS_PYTHON",
        str(INDEX_TTS_ROOT / ".venv" / "Scripts" / "python.exe"),
    )
)
INDEX_TTS_DEVICE = os.environ.get("INDEX_TTS_DEVICE", "cuda")
INDEX_TTS_FP16 = os.environ.get("INDEX_TTS_FP16", "true").lower() != "false"
INDEX_TTS_TIMEOUT_MS = int(os.environ.get("INDEX_TTS_TIMEOUT_MS", "600000"))
DEFAULT_VOICE = os.environ.get("INDEX_TTS_DEFAULT_VOICE", "gsv:008")
DEFAULT_EMOTION = os.environ.get(
    "INDEX_TTS_EMOTION_TEXT",
    "自然流畅的解说语气，情绪饱满",
)
EMOTION_WEIGHT = float(os.environ.get("INDEX_TTS_EMOTION_WEIGHT", "0.65"))
PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "indextts_proxy_api_key")
LISTEN_PORT = int(os.environ.get("APP_PORT", os.environ.get("LISTEN_PORT", "10003")))

_LMD_ROOT = Path(
    os.environ.get(
        "LOCALMINIDRAMA_ROOT",
        r"C:\project\LocalMiniDrama-main\LocalMiniDrama-main\backend-node",
    )
)
GPT_SOVITS_VOICES_FILE = Path(
    os.environ.get(
        "GPT_SOVITS_VOICES_FILE",
        str(_LMD_ROOT / "data" / "gptsovits" / "voices.json"),
    )
)
GPT_SOVITS_REF_ROOT = Path(
    os.environ.get(
        "GPT_SOVITS_REF_ROOT",
        str(_LMD_ROOT / "data" / "gptsovits-refs"),
    )
)

REQUIRED_MODEL_FILES = ("gpt.pth", "s2mel.pth", "config.yaml")

app = FastAPI(title="IndexTTS OpenAI Speech Proxy")
_synth_lock = asyncio.Lock()


def _python_bin() -> str:
    if INDEX_TTS_PYTHON.is_file():
        return str(INDEX_TTS_PYTHON)
    return "python"


def _missing_models() -> list[str]:
    return [name for name in REQUIRED_MODEL_FILES if not (INDEX_TTS_MODEL_DIR / name).is_file()]


def _auth_ok(authorization: str | None, x_api_key: str | None) -> bool:
    if x_api_key and x_api_key == PROXY_API_KEY:
        return True
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1] == PROXY_API_KEY
    return False


def _require_auth(authorization: str | None, x_api_key: str | None) -> None:
    if not _auth_ok(authorization, x_api_key):
        raise HTTPException(status_code=403, detail="Invalid authentication")


def _parse_voice_id(voice: str | None) -> str:
    raw = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    if raw.startswith("gsv:"):
        return raw[4:].strip() or "008"
    return raw


_REF_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
_REF_AUDIO_MAX_BYTES = 15 * 1024 * 1024


def _ensure_voice_catalog() -> None:
    GPT_SOVITS_VOICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    GPT_SOVITS_REF_ROOT.mkdir(parents=True, exist_ok=True)
    if not GPT_SOVITS_VOICES_FILE.is_file():
        GPT_SOVITS_VOICES_FILE.write_text(
            json.dumps({"voices": []}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _load_voice_catalog() -> list[dict[str, Any]]:
    _ensure_voice_catalog()
    try:
        catalog = json.loads(GPT_SOVITS_VOICES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="voice catalog invalid JSON") from exc
    voices = catalog.get("voices") if isinstance(catalog, dict) else None
    if not isinstance(voices, list):
        raise HTTPException(status_code=500, detail="voice catalog invalid")
    return [v for v in voices if isinstance(v, dict)]


def _write_voice_catalog(voices: list[dict[str, Any]]) -> None:
    _ensure_voice_catalog()
    GPT_SOVITS_VOICES_FILE.write_text(
        json.dumps({"voices": voices}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _next_voice_id(voices: list[dict[str, Any]]) -> str:
    used: set[int] = set()
    for entry in voices:
        raw = str(entry.get("voice_id") or "").strip()
        if raw.isdigit():
            used.add(int(raw))
    n = 1
    while n in used:
        n += 1
    return f"{n:03d}"


def _resolve_voice_path(voice: str | None) -> Path:
    voice_id = _parse_voice_id(voice)
    voices = _load_voice_catalog()
    entry = next((v for v in voices if str(v.get("voice_id")) == voice_id), None)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"unknown voice: {voice_id}")
    ref = str(entry.get("ref_audio_path") or "").strip()
    if not ref:
        raise HTTPException(status_code=500, detail=f"voice {voice_id} has empty ref_audio_path")
    path = Path(ref) if Path(ref).is_absolute() else GPT_SOVITS_REF_ROOT / ref
    if not path.is_file():
        raise HTTPException(status_code=500, detail=f"ref audio missing: {path}")
    return path


def _voice_options() -> list[dict[str, str]]:
    """Clone-voice catalog for ArcReel dropdowns (id is what /v1/audio/speech expects)."""
    out: list[dict[str, str]] = []
    for entry in _load_voice_catalog():
        vid = str(entry.get("voice_id") or "").strip()
        if not vid:
            continue
        name = str(entry.get("voice_name") or vid).strip() or vid
        out.append({"id": f"gsv:{vid}", "label": name})
    return out


def _safe_ref_filename(original: str) -> tuple[str, str]:
    name = Path(original or "ref.wav").name
    stem = "".join(ch if ch.isalnum() or ch in "._-() " or "\u4e00" <= ch <= "\u9fff" else "_" for ch in Path(name).stem)
    stem = stem.strip(" ._") or "ref"
    ext = Path(name).suffix.lower()
    if ext not in _REF_AUDIO_EXTS:
        raise HTTPException(
            status_code=400,
            detail="reference audio must be wav / mp3 / flac / ogg / m4a",
        )
    return stem, ext


def _run_cli(args: list[str], timeout_ms: int = INDEX_TTS_TIMEOUT_MS) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HUB_CACHE", str(INDEX_TTS_MODEL_DIR / "hf_cache"))
    cmd = [_python_bin(), "-m", "indextts.cli_v2", *args]
    logger.info("IndexTTS CLI: %s", " ".join(cmd[:6]) + " …")
    return subprocess.run(
        cmd,
        cwd=str(INDEX_TTS_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_ms / 1000.0,
        check=False,
    )


def _maybe_adjust_speed(wav_path: Path, speed: float | None) -> Path:
    if speed is None or abs(speed - 1.0) < 1e-6:
        return wav_path
    if speed <= 0 or not (0.5 <= speed <= 2.0):
        raise HTTPException(status_code=400, detail="speed must be in [0.5, 2.0]")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg missing; ignoring speed=%s", speed)
        return wav_path
    out = wav_path.with_name(wav_path.stem + f"_spd{speed:.2f}.wav")
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(wav_path),
            "-filter:a",
            f"atempo={speed}",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 44:
        raise HTTPException(status_code=500, detail="speed adjust failed")
    return out


def _convert_format(wav_path: Path, response_format: str) -> tuple[Path, str]:
    fmt = (response_format or "wav").lower().strip()
    if fmt in ("wav", "pcm"):
        return wav_path, "audio/wav"
    if fmt not in ("mp3", "opus", "aac", "flac"):
        fmt = "mp3"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return wav_path, "audio/wav"
    out = wav_path.with_suffix(f".{fmt}")
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(wav_path), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        return wav_path, "audio/wav"
    media = {
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "aac": "audio/aac",
        "flac": "audio/flac",
    }.get(fmt, "application/octet-stream")
    return out, media


async def _synthesize(text: str, voice: str | None, speed: float | None, response_format: str) -> tuple[bytes, str]:
    missing = _missing_models()
    if missing:
        raise HTTPException(status_code=503, detail=f"missing model files: {missing[0]}")
    if not (INDEX_TTS_ROOT / "pyproject.toml").is_file():
        raise HTTPException(status_code=503, detail=f"IndexTTS2 not installed: {INDEX_TTS_ROOT}")

    voice_path = _resolve_voice_path(voice)
    work = Path(tempfile.mkdtemp(prefix="indextts_speech_"))
    try:
        out_wav = work / f"synth_{uuid.uuid4().hex[:8]}.wav"
        args = [
            "synth",
            "--model-dir",
            str(INDEX_TTS_MODEL_DIR),
            "--text",
            text,
            "--voice",
            str(voice_path),
            "--emotion-text",
            DEFAULT_EMOTION,
            "--emotion-weight",
            str(EMOTION_WEIGHT),
            "--output",
            str(out_wav),
            "--force",
            "--device",
            INDEX_TTS_DEVICE,
        ]
        if INDEX_TTS_FP16:
            args.append("--fp16")

        result = await asyncio.to_thread(_run_cli, args)
        if result.returncode != 0 or not out_wav.is_file() or out_wav.stat().st_size < 44:
            detail = f"{result.stderr}\n{result.stdout}".strip()[-2000:]
            raise HTTPException(status_code=500, detail=detail or "IndexTTS2 synth failed")

        sped = await asyncio.to_thread(_maybe_adjust_speed, out_wav, speed)
        final_path, media_type = await asyncio.to_thread(_convert_format, sped, response_format)
        return final_path.read_bytes(), media_type
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.get("/health")
async def health() -> dict[str, Any]:
    missing = _missing_models()
    ok = not missing and (INDEX_TTS_ROOT / "pyproject.toml").is_file()
    return {
        "ok": ok,
        "root": str(INDEX_TTS_ROOT),
        "model_dir": str(INDEX_TTS_MODEL_DIR),
        "missing_models": missing,
        "default_voice": DEFAULT_VOICE,
        "voices_file": str(GPT_SOVITS_VOICES_FILE),
    }


@app.get("/v1/models")
async def list_models(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict[str, Any]:
    _require_auth(authorization, x_api_key)
    return {
        "object": "list",
        "data": [
            {
                "id": "indextts-2",
                "object": "model",
                "owned_by": "indextts",
            }
        ],
    }


@app.get("/v1/audio/voices")
async def list_audio_voices(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict[str, Any]:
    """OpenAI-compat extension: enumerate clone voices for TTS UI pickers."""
    _require_auth(authorization, x_api_key)
    voices = _voice_options()
    return {
        "object": "list",
        "voices": voices,
        "data": [{"id": v["id"], "name": v["label"]} for v in voices],
        "default": DEFAULT_VOICE,
        "supports_clone_voices": True,
    }


@app.post("/v1/audio/voices")
async def create_audio_voice(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    file: UploadFile = File(...),
    voice_name: str = Form(...),
    voice_id: str | None = Form(default=None),
    prompt_text: str = Form(default=""),
) -> dict[str, Any]:
    """Register a new IndexTTS clone voice: upload reference audio + catalog entry."""
    _require_auth(authorization, x_api_key)
    name = voice_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="voice_name is required")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty reference audio")
    if len(raw) > _REF_AUDIO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="reference audio exceeds 15MB")

    _stem, ext = _safe_ref_filename(file.filename or "ref.wav")
    voices = _load_voice_catalog()
    requested = (voice_id or "").strip().removeprefix("gsv:").strip()
    if requested:
        if any(str(v.get("voice_id") or "").strip() == requested for v in voices):
            raise HTTPException(status_code=409, detail=f"voice already exists: {requested}")
        new_id = requested
    else:
        new_id = _next_voice_id(voices)

    filename = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}{ext}"
    GPT_SOVITS_REF_ROOT.mkdir(parents=True, exist_ok=True)
    abs_path = GPT_SOVITS_REF_ROOT / filename
    abs_path.write_bytes(raw)

    entry = {
        "voice_id": new_id,
        "voice_name": name,
        "ref_audio_path": filename,
        "prompt_text": (prompt_text or "").strip(),
        "prompt_lang": "zh",
        "language": "中文",
        "description": [],
        "text_split_method": "cut5",
    }
    voices.append(entry)
    _write_voice_catalog(voices)
    option_id = f"gsv:{new_id}"
    logger.info("registered clone voice %s (%s) → %s", option_id, name, abs_path)
    return {
        "ok": True,
        "voice": {"id": option_id, "label": name},
        "voice_id": option_id,
        "voice_name": name,
        "ref_audio_path": filename,
    }


@app.get("/v1/audio/voices/{voice_id}/reference")
async def get_audio_voice_reference(
    voice_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Response:
    """Return the clone voice's original reference audio bytes (registration source)."""
    _require_auth(authorization, x_api_key)
    path = _resolve_voice_path(voice_id)
    ext = path.suffix.lower()
    media_types = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
    }
    media_type = media_types.get(ext, "application/octet-stream")
    return Response(
        content=path.read_bytes(),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@app.delete("/v1/audio/voices/{voice_id}")
async def delete_audio_voice(
    voice_id: str,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict[str, Any]:
    """Remove a clone voice from the catalog (does not delete shared stock voices forcibly beyond catalog)."""
    _require_auth(authorization, x_api_key)
    parsed = _parse_voice_id(voice_id)
    voices = _load_voice_catalog()
    kept: list[dict[str, Any]] = []
    removed: dict[str, Any] | None = None
    for entry in voices:
        if str(entry.get("voice_id") or "").strip() == parsed:
            removed = entry
            continue
        kept.append(entry)
    if removed is None:
        raise HTTPException(status_code=404, detail=f"voice not found: {parsed}")
    _write_voice_catalog(kept)
    ref = str(removed.get("ref_audio_path") or "").strip()
    if ref:
        path = Path(ref) if Path(ref).is_absolute() else GPT_SOVITS_REF_ROOT / ref
        try:
            resolved = path.resolve()
            root = GPT_SOVITS_REF_ROOT.resolve()
            if resolved.is_file() and str(resolved).startswith(str(root) + os.sep):
                path.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to delete ref audio %s", path, exc_info=True)
    return {"ok": True, "deleted": True, "voice_id": f"gsv:{parsed}"}


@app.post("/v1/audio/speech")
async def audio_speech(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Response:
    _require_auth(authorization, x_api_key)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")

    text = str(body.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="input is required")

    voice = body.get("voice")
    voice_s = str(voice).strip() if voice is not None else None
    speed_raw = body.get("speed")
    speed: float | None
    try:
        speed = float(speed_raw) if speed_raw is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid speed") from exc

    response_format = str(body.get("response_format") or "wav")

    async with _synth_lock:
        audio_bytes, media_type = await _synthesize(text, voice_s, speed, response_format)

    return Response(content=audio_bytes, media_type=media_type)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def catch_all(path: str) -> JSONResponse:
    if path in ("", "favicon.ico"):
        return JSONResponse({"ok": True, "service": "indextts_speech_proxy"})
    raise HTTPException(status_code=404, detail=f"unknown path: /{path}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=LISTEN_PORT)
