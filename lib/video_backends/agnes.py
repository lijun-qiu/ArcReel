"""AgnesVideoBackend — Agnes 视频生成后端（异步轮询 + resume）。

走 apihub 网关上的 OpenAI 风格异步端点：submit ``POST /v1/videos``（JSON）取 task_id →
轮询至 ``status=completed``。成片 URL 分两级取：完成态响应自带直接 URL 字段（``url`` /
``video_url`` / ``metadata.url``，或 ``remixed_from_video_id`` 恰好是 URL 形态时兼容旧网关）
即直接下载；只有 ``video_id`` 时以其向网关根下的成片查询端点 ``GET /agnesapi?video_id=...``
二次查询。``remixed_from_video_id`` 语义是 remix 来源视频 ID，非 URL 形态时一律不当下载地址。
状态机 ``queued → in_progress → completed / failed``。

型号分流：

- ``agnes-video-v2.0``：裸 base64；时长经 ``num_frames``/``frame_rate``；**参考图上限 1**
  （顶层 ``image`` 图生）；真正的首+尾帧才走 ``extra_body`` + ``mode=keyframes``。
  勿把多资产参考塞进 keyframes（那是插值通道，不是身份参考）。轮询可用
  ``/v1/videos/{task_id}``。
- ``agnes-video-2.5`` / ``agnes-video-2.5-flash``：官方 Videos 兼容契约；必填 ``mode``
  （``text`` / ``keyframe`` / ``reference``）；输出档 ``size=720P``；参考图走顶层
  ``images``（data URI，上限 5），参考音频走顶层 ``audios``（data URI，上限 3），
  二者任一非空即 ``mode=reference``（与首/尾帧互斥，与官网一致）。时长 2.5 为 4–10s，
  Flash 为 4–12s。Flash 不支持参考视频。轮询走 ``/agnesapi?video_id=&model_name=``
  （文档推荐）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from lib.agnes_shared import agnes_base_url, agnes_headers, agnes_host, resolve_agnes_api_key
from lib.aspect_size import VIDEO_TIER_SHORT_EDGE, aspect_size, resolution_to_short_edge
from lib.data_uri import image_to_data_uri
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_AGNES
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    ProviderJobIdPersistenceMixin,
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
    reference_audio_to_data_uri,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "agnes-video-v2.0"
MODEL_V25 = "agnes-video-2.5"
MODEL_V25_FLASH = "agnes-video-2.5-flash"

_VIDEOS_ENDPOINT = "/videos"
# 成片查询端点，挂在网关根（不在 /v1 下），按 video_id 查询。
_VIDEO_QUERY_ENDPOINT = "/agnesapi"

# fps 固定 24；num_frames 必须形如 8n+1。1080p 9:16 上游上限 241 帧（≈10s @24fps）。
# 时长按秒 × fps 取整后对齐到最近的 8n+1。1–3s 会落到 81 帧以下（25/49/73）。
_FPS = 24
_FRAME_STEP = 8
_MAX_NUM_FRAMES = 241

# v2.0 时长边界，与 registry supported_durations（1..10s）同步。
_V20_MIN_DURATION_SECONDS = 1
_V20_MAX_DURATION_SECONDS = 10
# v2.0 参考图上限：官方只有「单图 i2v」与「keyframes 插值」两种带图模式。
# keyframes 是首→尾时间轴插值，不能拿角色/场景/道具资产当「多主体参考」——否则会
# 在无关定妆图之间变形，观感像倒退/乱跳。多主体参考请用 v2.5 ``mode=reference``。
_V20_MAX_REFERENCE_IMAGES = 1

# v2.5：seconds "4"–"10"；size 目前仅 720P；reference 图按计费文案「前 5 张免费」取 5 为编排上限。
# 参考音频：Flash 文档上限 3；2.5 沿用同一上限（官网 Flash 专属差异表列出 audios，2.5 未给更高值）。
# 出处：https://www.agnes-ai.com/zh-Hans/docs/agnes-video-25-flash
_V25_MIN_DURATION_SECONDS = 4
_V25_MAX_DURATION_SECONDS = 10
# Flash：seconds "4"–"12"（其余契约与 2.5 相同）。
_V25_FLASH_MAX_DURATION_SECONDS = 12
_V25_MAX_REFERENCE_IMAGES = 5
_V25_MAX_REFERENCE_AUDIO = 3
_V25_SIZE = "720P"
_V25_MODE_TEXT = "text"
_V25_MODE_KEYFRAME = "keyframe"
_V25_MODE_REFERENCE = "reference"

# 参考音频 data URI MIME：与 ark 同口径（官方示例 .mp3；wav/mp3 均可）。各 backend 自持 MIME 表。
_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mp3"}

# 编排层统一写 ``@图片N`` / ``@音频N``；Agnes reference 模式文档要求 ``<Picture N>`` / ``<Audio N>``。
_V25_PICTURE_TAG_RE = re.compile(r"@图片(\d+)")
_V25_AUDIO_TAG_RE = re.compile(r"@音频(\d+)")

# 兼容旧常量名（测试 / 外部引用）。
_MIN_DURATION_SECONDS = _V20_MIN_DURATION_SECONDS
_MAX_DURATION_SECONDS = _V20_MAX_DURATION_SECONDS
_MAX_REFERENCE_IMAGES = _V20_MAX_REFERENCE_IMAGES

# 尺寸约束：长宽被 8 整除、长边收口 1920（保守值，覆盖上游 480p/720p/1080p 三档标准化）。
# 缺 resolution 时按 720p 短边兜底。待 console / 实测核对像素上限，不硬编当既成事实。
_VIDEO_ROUND_TO = 8
_MAX_LONG_EDGE = 1920

# submit 超时 ~300s：覆盖上游争用时的长阻塞，避免可重试的繁忙被 ReadTimeout 包成终态歧义失败。
_SUBMIT_TIMEOUT_SECONDS = 300.0
# 轮询 / 下载用较短超时（幂等 GET 正常秒级返回）。
_POLL_HTTP_TIMEOUT_SECONDS = 60.0

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0

_KEYFRAMES_MODE = "keyframes"


def _is_v25(model: str) -> bool:
    return model == MODEL_V25 or model.startswith("agnes-video-2.5")


def _is_v25_flash(model: str) -> bool:
    return model == MODEL_V25_FLASH or model.startswith("agnes-video-2.5-flash")


def _v25_max_duration(model: str) -> int:
    return _V25_FLASH_MAX_DURATION_SECONDS if _is_v25_flash(model) else _V25_MAX_DURATION_SECONDS


# 失败终态集合：除文档化的 failed 外，纳入 error / cancelled / canceled，避免上游以非标准失败态
# 收尾时被当「仍在进行」轮询到超时。
_FAILED_STATUSES = ("failed", "error", "cancelled", "canceled")

# 进日志的安全标量白名单；image / images / extra_body 内的 base64 一律不入日志。
_SAFE_LOG_KEYS = (
    "model",
    "height",
    "width",
    "num_frames",
    "frame_rate",
    "seed",
    "mode",
    "seconds",
    "size",
    "aspect_ratio",
)

# 完成态响应中可能承载成片 URL 的权威字段，按优先级探测（顶层与 metadata 同权，顶层优先）。
_PRIMARY_URL_FIELDS = ("url", "video_url")

# remix 来源视频 ID 字段。语义不是 URL（非 URL 形态时不当下载地址），列在此处仅为兼容部分
# 网关把成片 URL 直接回填在该字段的行为；优先级低于 _PRIMARY_URL_FIELDS——顶层与 metadata
# 的权威字段任一命中都优先于本字段，避免它抢在真正的成片 URL 前面被当下载地址。
_COMPAT_URL_FIELD = "remixed_from_video_id"


def _looks_like_url(value: str) -> bool:
    """粗粒度 URL 形态校验：http/https scheme + 非空 netloc。用于把 remixed_from_video_id
    这类语义不是 URL 的字段与真正的下载地址区分开，避免把 remix 来源 ID 误当 URL 下载。
    """
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _first_url_field(body: dict) -> str | None:
    """探测响应体中形态为 URL 的成片地址，按 _PRIMARY_URL_FIELDS 优先、_COMPAT_URL_FIELD
    兜底的顺序；每一级顶层与 ``metadata``（网关成片查询把下载地址放在 metadata.url）同权、
    顶层优先，无命中返回 None。

    _COMPAT_URL_FIELD 兜底级必须整体排在 _PRIMARY_URL_FIELDS 之后：否则顶层的兼容字段会
    抢在 metadata 里的权威 URL 字段前面命中，误把兼容字段值当下载地址。
    """
    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    for key in _PRIMARY_URL_FIELDS:
        for source in (body, metadata):
            value = source.get(key)
            if isinstance(value, str) and _looks_like_url(value):
                return value

    for source in (body, metadata):
        value = source.get(_COMPAT_URL_FIELD)
        if isinstance(value, str) and _looks_like_url(value):
            return value

    return None


def _duration_to_num_frames(duration_seconds: int) -> int:
    """秒 → num_frames：秒 × fps 取整后对齐到最近的 ``8n+1``，上限 241。"""
    target = max(1, duration_seconds) * _FPS
    n = round((target - 1) / _FRAME_STEP)
    num_frames = _FRAME_STEP * n + 1
    return max(1, min(num_frames, _MAX_NUM_FRAMES))


def _resolve_size(resolution: str | None, aspect_ratio: str) -> tuple[int, int]:
    """比例优先、清晰度其次：短边来自 resolution（档位 / 自定义 / None 兜底 720p），
    比例精确来自 aspect_ratio、长宽被 8 整除、长边收口 1920。返回 (宽, 高)。
    """
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    return aspect_size(aspect_ratio, short, round_to=_VIDEO_ROUND_TO, max_long_edge=_MAX_LONG_EDGE)


def _image_to_bare_base64(image_path: Path) -> str:
    """本地图片 → **裸 base64** 字符串（无 ``data:`` 前缀）。

    Agnes 视频端对整串做 base64 解码，带 ``data:`` 前缀会在生成期触发 padding 错误，故不复用
    仓库通用 data-URI helper（图像端接受 data-URI，视频端不接受，二者不可混用）。
    """
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _safe_body_for_log(body: dict) -> dict:
    """安全日志视图：白名单标量 + prompt 仅长度 + 图像仅计数（base64 / data URI 不入日志）。"""
    view: dict = {key: body[key] for key in _SAFE_LOG_KEYS if key in body}
    prompt = body.get("prompt")
    if isinstance(prompt, str):
        view["prompt_len"] = len(prompt)
    if body.get("image"):
        view["image"] = "<start_frame>"
    if body.get("first_frame"):
        view["first_frame"] = "<frame>"
    if body.get("last_frame"):
        view["last_frame"] = "<frame>"
    images = body.get("images")
    if isinstance(images, list):
        view["images"] = f"<{len(images)} img>"
    audios = body.get("audios")
    if isinstance(audios, list):
        view["audios"] = f"<{len(audios)} audio>"
    extra = body.get("extra_body")
    if isinstance(extra, dict) and isinstance(extra.get("image"), list):
        mode = extra.get("mode")
        view["extra_body"] = f"<{len(extra['image'])} img{f', mode={mode}' if mode else ''}>"
    return view


def _adapt_v25_reference_prompt(prompt: str) -> str:
    """把共享编排标签改写成 Agnes reference 文档标签，便于模型解析素材指代。"""
    adapted = _V25_PICTURE_TAG_RE.sub(r"<Picture \1>", prompt)
    return _V25_AUDIO_TAG_RE.sub(r"<Audio \1>", adapted)


def _extract_task_id(body: dict) -> str:
    """从提交响应取轮询用 task_id（``task_id`` 优先，回落 ``id``）。"""
    for key in ("task_id", "id"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    # 仅暴露字段名，不回显整串响应（可能含 prompt / 签名 URL 等敏感字段，与 _safe_body_for_log 同口径）。
    raise RuntimeError(f"Agnes 视频提交返回体缺少 task_id（字段: {sorted(body)}）")


def _extract_duration_seconds(final: dict, queried: dict | None, fallback: int) -> int:
    """从轮询终态取实际成片时长（顶层 ``seconds``），缺失时改读 video_id 二次查询响应的
    ``seconds``（完成态只带 video_id 时才有此响应），两处均缺失或不可解析才回落请求时长。

    不读 ``usage.duration_seconds``——该字段是任务处理耗时，与成片时长无关，读它会错记
    计费与元数据。
    """
    parsed = _coerce_duration(final.get("seconds"))
    if parsed is not None:
        return parsed
    if queried is not None:
        parsed = _coerce_duration(queried.get("seconds"))
        if parsed is not None:
            return parsed
    return fallback


def _coerce_duration(value: object) -> int | None:
    """把 ``"10.0"`` / ``10`` 这类时长值归一化为计费秒数：half-up 取整（4.5→5，不少计），
    非正值 / 超 24h 上限（防 DB Integer 列溢出）/ 不可解析一律回 None，由 caller 回落请求时长。
    """
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
        if not 0 < decimal_value <= MAX_BILLED_DURATION_SECONDS:
            return None
        return int(decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _failure_reason(state: dict) -> str | None:
    """失败终态（failed / error / cancelled / canceled）→ 错误描述；其余 → None。

    不止认 ``failed``：上游若以其他失败态收尾，仅认 completed/failed 会把它当「仍在进行」轮询到
    max_wait 才抛误导性 TimeoutError，白占 worker 通道；显式枚举失败态让其快速失败。
    """
    if state.get("status") not in _FAILED_STATUSES:
        return None
    err = state.get("error")
    if isinstance(err, dict):
        message = err.get("message") or err.get("code") or "unknown"
    else:
        message = err or "unknown"
    return f"Agnes 视频生成失败: {message}"


class AgnesVideoBackend(ProviderJobIdPersistenceMixin):
    """Agnes 视频后端（异步 submit/poll，裸 base64 图像，支持 resume）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        http_timeout: float = _POLL_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = resolve_agnes_api_key(api_key)
        self._base_url = agnes_base_url(base_url)
        self._host = agnes_host(base_url)
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout

    @property
    def name(self) -> str:
        return PROVIDER_AGNES

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 SDK client（无需 api_key）。

        首帧 + 尾帧；v2.0 参考图上限 1（单图 i2v）；v2.5 参考图上限 5、参考音频上限 3
        （mode=reference，与官网 Flash 专属限制一致）。参考图/参考音频不与首帧叠加（单通道）。
        编排层按此裁剪。
        """
        if _is_v25(model):
            return VideoCapabilities(
                first_frame=True,
                last_frame=True,
                max_reference_images=_V25_MAX_REFERENCE_IMAGES,
                reference_audio_mode=ReferenceAudioMode.DIRECT,
                max_reference_audio_count=_V25_MAX_REFERENCE_AUDIO,
            )
        return VideoCapabilities(
            first_frame=True,
            last_frame=True,
            max_reference_images=_V20_MAX_REFERENCE_IMAGES,
        )

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        # 读盘 + base64 编码（首尾帧最多 2 张、参考图按型号上限）offload 到线程，
        # 避免阻塞共享 worker 事件循环（与 image 后端及 grok/gemini 视频后端一致）。
        payload = await asyncio.to_thread(self._build_payload, request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(_safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("Agnes 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            await self._persist_provider_job_id(request, task_id, provider=PROVIDER_AGNES)
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 Agnes task：仅轮询 + 下载，不重新提交（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        """构建提交体；按型号分流到 v2.0 / v2.5 契约。"""
        if _is_v25(self._model):
            return self._build_payload_v25(request)
        return self._build_payload_v20(request)

    def _build_payload_v20(self, request: VideoGenerationRequest) -> dict:
        """v2.0 提交体。

        通道优先级（单通道，不叠加）：
        - 1 张参考图 → 顶层 ``image``（图生 / 主体锚定；v2.0 无多图 reference 模式）
        - 首+尾帧 → ``extra_body.image=[s,e]`` + ``mode=keyframes``（时间轴插值）
        - 仅起始图 → 顶层 ``image``
        - 都无 → 文生视频

        ``mode=keyframes`` **只**用于真正的首尾帧，不用来塞角色/场景/道具多参考。
        """
        self._reject_out_of_range_duration(
            request.duration_seconds,
            minimum=_V20_MIN_DURATION_SECONDS,
            maximum=_V20_MAX_DURATION_SECONDS,
        )
        width, height = _resolve_size(request.resolution, request.aspect_ratio)
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "height": height,
            "width": width,
            "num_frames": _duration_to_num_frames(request.duration_seconds),
            "frame_rate": _FPS,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        reference_images = self._valid_paths(request.reference_images)
        start_image = self._single_path(request.start_image)
        end_image = self._single_path(request.end_image)
        self._reject_mixed_channels(reference_images, [], start_image, end_image)

        if reference_images:
            if len(reference_images) > _V20_MAX_REFERENCE_IMAGES:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=_V20_MAX_REFERENCE_IMAGES,
                )
            # 单图 i2v：顶层 image，不设 mode。切勿把多资产参考改走 keyframes。
            payload["image"] = self._encode_reference(reference_images[0])
        elif start_image is not None and end_image is not None:
            payload["extra_body"] = {
                "image": [self._encode_start(start_image), self._encode_end(end_image)],
                "mode": _KEYFRAMES_MODE,
            }
        elif start_image is not None:
            payload["image"] = self._encode_start(start_image)

        return payload

    def _build_payload_v25(self, request: VideoGenerationRequest) -> dict:
        """v2.5 提交体：必填 mode；参考图走 ``images``；参考音频走 ``audios``；关键帧走
        ``first_frame``/``last_frame``。

        本地文件以 data URI 提交（项目资产无公网 URL）；与图像后端同口径。
        ``images`` / ``audios`` 与关键帧互斥（官网 mode 规则）；任一参考通道非空即
        ``mode=reference``（可仅音频、仅图片，或二者兼有）。
        """
        self._reject_out_of_range_duration(
            request.duration_seconds,
            minimum=_V25_MIN_DURATION_SECONDS,
            maximum=_v25_max_duration(self._model),
        )
        payload: dict = {
            "model": self._model,
            "prompt": request.prompt,
            "seconds": str(int(request.duration_seconds)),
            "size": _V25_SIZE,
            "aspect_ratio": request.aspect_ratio or "16:9",
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        reference_images = self._valid_paths(request.reference_images)
        reference_audios = self._valid_paths(request.reference_audio_files)
        start_image = self._single_path(request.start_image)
        end_image = self._single_path(request.end_image)
        self._reject_mixed_channels(reference_images, reference_audios, start_image, end_image)

        if reference_images or reference_audios:
            if len(reference_images) > _V25_MAX_REFERENCE_IMAGES:
                raise VideoCapabilityError(
                    "video_reference_images_exceeded",
                    model=self._model,
                    count=len(reference_images),
                    limit=_V25_MAX_REFERENCE_IMAGES,
                )
            if len(reference_audios) > _V25_MAX_REFERENCE_AUDIO:
                raise VideoCapabilityError(
                    "video_reference_audio_exceeded",
                    model=self._model,
                    count=len(reference_audios),
                    limit=_V25_MAX_REFERENCE_AUDIO,
                )
            payload["mode"] = _V25_MODE_REFERENCE
            payload["prompt"] = _adapt_v25_reference_prompt(request.prompt)
            if reference_images:
                payload["images"] = [self._encode_media_url(p, role="reference") for p in reference_images]
            if reference_audios:
                payload["audios"] = [self._encode_reference_audio(p) for p in reference_audios]
        elif start_image is not None and end_image is not None:
            payload["mode"] = _V25_MODE_KEYFRAME
            payload["first_frame"] = self._encode_media_url(start_image, role="start")
            payload["last_frame"] = self._encode_media_url(end_image, role="end")
        elif start_image is not None:
            payload["mode"] = _V25_MODE_KEYFRAME
            payload["first_frame"] = self._encode_media_url(start_image, role="start")
        else:
            payload["mode"] = _V25_MODE_TEXT

        return payload

    def _reject_mixed_channels(
        self,
        reference_images: list[Path],
        reference_audios: list[Path],
        start_image: Path | None,
        end_image: Path | None,
    ) -> None:
        """参考图/参考音频与首/尾帧互斥；只给尾帧无首帧时 fail-loud。

        官网：``keyframe`` 不允许 ``images``/``audios``；``reference`` 不允许 ``first_frame``/
        ``last_frame``。参考音频同属 reference 通道，与帧混合同样提前拦截。
        """
        if (reference_images or reference_audios) and (start_image is not None or end_image is not None):
            raise VideoCapabilityError("video_reference_images_with_frames_unsupported", model=self._model)
        if end_image is not None and start_image is None:
            raise VideoCapabilityError("video_end_image_requires_start_image", model=self._model)

    def _reject_out_of_range_duration(
        self,
        duration_seconds: int,
        *,
        minimum: int,
        maximum: int,
    ) -> None:
        """时长越界时 fail-loud；上游若漏校验，避免静默截帧 + 错记计费时长。"""
        if not minimum <= duration_seconds <= maximum:
            raise VideoCapabilityError(
                "video_duration_not_supported",
                model=self._model,
                duration=duration_seconds,
                supported=f"{minimum}-{maximum}",
            )

    @staticmethod
    def _single_path(value: str | Path | None) -> Path | None:
        """把请求里的图像字段归一化成 Path；空 / 空串 / 空 Path（``Path("")`` 会塌成 ``Path(".")``）→ None。"""
        if value is None:
            return None
        text = str(value)
        if not text or text == ".":
            return None
        return Path(text)

    @classmethod
    def _valid_paths(cls, values: list[Path] | None) -> list[Path]:
        """归一化参考图/音频列表：剔除空 / 空 Path（``[Path(v) for v if v]`` 对 Path 恒真，不起过滤作用）。"""
        return [p for v in (values or []) if (p := cls._single_path(v)) is not None]

    def _encode_start(self, path: Path) -> str:
        """裸 base64 编码首帧；缺失或不可读 fail-loud（不静默退化为文生视频）。"""
        return self._encode_image(path, error_code="video_start_image_unreadable", name=path.name or str(path))

    def _encode_end(self, path: Path) -> str:
        """裸 base64 编码尾帧；缺失或不可读 fail-loud（错误指向尾帧而非首帧）。"""
        return self._encode_image(path, error_code="video_end_image_unreadable", name=path.name or str(path))

    def _encode_reference(self, path: Path) -> str:
        """裸 base64 编码参考图；缺失或不可读 fail-loud（不静默丢弃后照常计费）。"""
        return self._encode_image(path, error_code="video_reference_images_unreadable", names=path.name or str(path))

    def _encode_media_url(self, path: Path, *, role: str) -> str:
        """v2.5 媒体字段：本地文件 → data URI（文档要求 URL 形态；项目资产无公网地址）。"""
        error_code = {
            "start": "video_start_image_unreadable",
            "end": "video_end_image_unreadable",
            "reference": "video_reference_images_unreadable",
        }.get(role, "video_reference_images_unreadable")
        err_params = {"name": path.name or str(path)} if role != "reference" else {"names": path.name or str(path)}
        if not path.is_file():
            raise VideoCapabilityError(error_code, model=self._model, **err_params)
        try:
            return image_to_data_uri(path, IMAGE_MIME_TYPES)
        except OSError as exc:
            raise VideoCapabilityError(error_code, model=self._model, **err_params) from exc

    def _encode_reference_audio(self, path: Path) -> str:
        """v2.5 参考音频 → data URI；格式/可读性失败 fail-loud（不跳过，以免 @音频N 错位）。"""
        return reference_audio_to_data_uri(path, model=self._model, mime_types=_REFERENCE_AUDIO_MIME_TYPES)

    def _encode_image(self, path: Path, *, error_code: str, **err_params: str) -> str:
        """裸 base64 编码图像；缺失或不可读时按通道 error_code / 参数名 fail-loud。"""
        if not path.is_file():
            raise VideoCapabilityError(error_code, model=self._model, **err_params)
        try:
            return _image_to_bare_base64(path)
        except OSError as exc:
            raise VideoCapabilityError(error_code, model=self._model, **err_params) from exc

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        # 非幂等的「建任务 + 计费」POST：submit_post 把歧义传输错误转 AmbiguousSubmitError 终态失败，
        # 避免重试重复建任务 + 重复计费；>=400 抛 HTTPStatusError 交 should_retry_submit 按状态码分流
        # （5xx/408/429 重试——含上游繁忙 503；确定性 4xx 快失败）。submit 用长超时覆盖上游长阻塞。
        resp = await submit_post(
            lambda: client.post(
                f"{self._base_url}{_VIDEOS_ENDPOINT}",
                json=payload,
                headers=agnes_headers(self._api_key),
                timeout=_SUBMIT_TIMEOUT_SECONDS,
            ),
            provider=PROVIDER_AGNES,
        )
        return _extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str) -> dict:
        """轮询一次任务状态。

        v2.5 / Flash 文档推荐 ``GET /agnesapi?video_id=&model_name=``；v2.0 仍可用
        ``/v1/videos/{task_id}``。
        """
        if _is_v25(self._model):
            return await self._query_video(client, task_id)
        resp = await client.get(
            f"{self._base_url}{_VIDEOS_ENDPOINT}/{task_id}",
            headers=agnes_headers(self._api_key),
        )
        resp.raise_for_status()
        return resp.json()

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_poll,
    )
    async def _query_video(self, client: httpx.AsyncClient, video_id: str) -> dict:
        """按 ``video_id`` 向成片查询端点二次查询（完成态只含 video_id、无直接 URL 字段时）。

        该端点挂在网关根而非 ``/v1`` 下。v2.5 / Flash 文档要求附带 ``model_name``（keyframe /
        reference 必填；text 也推荐）。幂等 GET，复用轮询同一套重试判定。
        """
        params: dict[str, str] = {"video_id": video_id}
        if _is_v25(self._model):
            params["model_name"] = self._model
        resp = await client.get(
            f"{self._host}{_VIDEO_QUERY_ENDPOINT}",
            params=params,
            headers=agnes_headers(self._api_key),
        )
        resp.raise_for_status()
        return resp.json()

    async def _resolve_video_url(self, client: httpx.AsyncClient, final: dict) -> tuple[str, dict | None]:
        """成片 URL 两级来源：完成态直接字段命中即用；否则用 video_id 二次查询取 URL。

        命中二次查询时一并返回该查询响应体（未查询则 None），供调用方从中补解析成片时长——
        终态响应可能不带 ``seconds``，只有二次查询响应才带。

        两级来源均不可用时报错信息只列字段名，不回显响应体（可能含签名 URL 等敏感字段，
        与 _safe_body_for_log 同口径）。
        """
        video_url = _first_url_field(final)
        if video_url is not None:
            return video_url, None

        video_id = final.get("video_id") or final.get("id")
        if isinstance(video_id, str) and video_id:
            queried = await self._query_video(client, video_id)
            video_url = _first_url_field(queried)
            if video_url is not None:
                return video_url, queried
            raise RuntimeError(f"Agnes 任务完成但 video_id 查询响应缺少成片 URL（字段: {sorted(queried)}）")

        raise RuntimeError(f"Agnes 任务完成但缺少成片 URL 与 video_id（字段: {sorted(final)}）")

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # resume 路径下 404 直接转 ResumeExpiredError：should_retry_poll 把轮询 404 当「短暂未就绪」
        # 重试，对已过期的 resume 任务会一直重到超时、永不落终态，故在此一击转终态异常。非 resume 的
        # 4xx 原样抛出，交 should_retry_poll 按 status_code 分流。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, task_id)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=task_id, provider=PROVIDER_AGNES) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=lambda state: state.get("status") in ("completed", "failed"),
            is_failed=_failure_reason,
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="Agnes",
            on_progress=lambda v, elapsed: logger.info(
                "Agnes 视频生成中... status=%s progress=%s elapsed=%ds",
                v.get("status"),
                v.get("progress"),
                int(elapsed),
            ),
        )

        video_url, queried = await self._resolve_video_url(client, final)

        await self._download_with_retry(video_url, request.output_path)
        logger.info("Agnes 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_AGNES,
            model=self._model,
            duration_seconds=_extract_duration_seconds(final, queried, request.duration_seconds),
            video_uri=video_url,
            task_id=task_id,
            seed=request.seed,
            # Agnes 提交体无音频字段；成片是否带声取决于上游。固定 False 与 kling/vidu
            # 无声模型一致，避免下游（计费/版本元数据/剪映导出）误判有声。
            generate_audio=False,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        """下载成片 URL（幂等 GET），独立的下载重试范围，不回退到重跑生成 POST。"""
        await download_video(video_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
