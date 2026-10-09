import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Clapperboard, Download, Loader2, Maximize2, Play, X } from "lucide-react";
import { API } from "@/api";
import { useRealEsrganProgress } from "@/components/canvas/reference/useRealEsrganProgress";

export interface EpisodePreviewSubtitleCue {
  start_microseconds: number;
  duration_microseconds: number;
  text: string;
}

export interface EpisodePreviewPlayback {
  url: string;
  revision?: number | null;
  subtitles_webvtt?: string | null;
  subtitles?: EpisodePreviewSubtitleCue[];
}

export interface EpisodePreviewActionsProps {
  episode: number;
  /** 本集镜头均已有视频时才可合成。 */
  canCompose: boolean;
  onComposePreview?: () => Promise<EpisodePreviewPlayback>;
  onUpscalePreview?: () => Promise<EpisodePreviewPlayback>;
  onWatchPreview?: () => Promise<EpisodePreviewPlayback | null>;
  onWatchUpscaled?: () => Promise<EpisodePreviewPlayback | null>;
  episodePreviewUrl?: string | null;
  episodeUpscaledUrl?: string | null;
  episodePreviewSubtitlesWebvtt?: string | null;
  episodePreviewSubtitles?: EpisodePreviewSubtitleCue[];
}

/**
 * 合成本集预览 / 播放 / 超分控件 + 播放对话框。
 * 时间轴（分镜生视频）与参考生视频集头共用。
 */
export function EpisodePreviewActions({
  episode,
  canCompose,
  onComposePreview,
  onUpscalePreview,
  onWatchPreview,
  onWatchUpscaled,
  episodePreviewUrl = null,
  episodeUpscaledUrl = null,
  episodePreviewSubtitlesWebvtt = null,
  episodePreviewSubtitles = [],
}: EpisodePreviewActionsProps) {
  const { t } = useTranslation("dashboard");
  const [composeBusy, setComposeBusy] = useState(false);
  const [upscaleBusy, setUpscaleBusy] = useState(false);
  const [watchBusy, setWatchBusy] = useState(false);
  const [watchUpscaledBusy, setWatchUpscaledBusy] = useState(false);
  const upscaleProgress = useRealEsrganProgress(upscaleBusy);
  const upscaleBusyLabel =
    upscaleBusy && upscaleProgress && upscaleProgress.percent > 0
      ? t("reference_episode_upscale_preview_busy_pct", {
          percent: Math.round(upscaleProgress.percent),
        })
      : t("reference_episode_upscale_preview_busy");
  const [watching, setWatching] = useState<EpisodePreviewPlayback | null>(null);
  const [positionMicroseconds, setPositionMicroseconds] = useState(0);

  const canWatch = Boolean(episodePreviewUrl || onWatchPreview);
  const canWatchUpscaled = Boolean(episodeUpscaledUrl || onWatchUpscaled);
  const canUpscale = Boolean(onUpscalePreview && (episodePreviewUrl || onWatchPreview));
  const anyBusy = composeBusy || upscaleBusy || watchBusy || watchUpscaledBusy;

  const openWatch = (playback: EpisodePreviewPlayback) => {
    setPositionMicroseconds(0);
    setWatching(playback);
  };

  const handleCompose = async () => {
    if (!onComposePreview || anyBusy || !canCompose) return;
    setComposeBusy(true);
    try {
      const result = await onComposePreview();
      openWatch({
        url: `${result.url}?v=${result.revision ?? Date.now()}`,
        subtitles_webvtt: result.subtitles_webvtt,
        subtitles: result.subtitles,
      });
    } catch {
      // toast by canvas
    } finally {
      setComposeBusy(false);
    }
  };

  const handleUpscale = async () => {
    if (!onUpscalePreview || anyBusy || !canUpscale) return;
    setUpscaleBusy(true);
    try {
      try {
        await API.ensureRealEsrgan();
      } catch {
        // best-effort
      }
      const result = await onUpscalePreview();
      openWatch({
        url: result.url.includes("?v=")
          ? result.url
          : `${result.url}?v=${result.revision ?? Date.now()}`,
        subtitles_webvtt: result.subtitles_webvtt ?? episodePreviewSubtitlesWebvtt,
        subtitles: result.subtitles ?? episodePreviewSubtitles,
      });
    } catch {
      // toast by canvas
    } finally {
      setUpscaleBusy(false);
    }
  };

  const handleWatch = async () => {
    if (anyBusy || !canWatch) return;
    setWatchBusy(true);
    try {
      if (onWatchPreview) {
        const result = await onWatchPreview();
        if (result) {
          openWatch({
            url: result.url.includes("?v=")
              ? result.url
              : `${result.url}?v=${result.revision ?? Date.now()}`,
            subtitles_webvtt: result.subtitles_webvtt ?? episodePreviewSubtitlesWebvtt,
            subtitles: result.subtitles ?? episodePreviewSubtitles,
          });
        }
        return;
      }
      if (!episodePreviewUrl) return;
      openWatch({
        url: episodePreviewUrl,
        subtitles_webvtt: episodePreviewSubtitlesWebvtt,
        subtitles: episodePreviewSubtitles,
      });
    } finally {
      setWatchBusy(false);
    }
  };

  const handleWatchUpscaled = async () => {
    if (anyBusy || !canWatchUpscaled) return;
    setWatchUpscaledBusy(true);
    try {
      if (onWatchUpscaled) {
        const result = await onWatchUpscaled();
        if (result) {
          openWatch({
            url: result.url.includes("?v=")
              ? result.url
              : `${result.url}?v=${result.revision ?? Date.now()}`,
            subtitles_webvtt: result.subtitles_webvtt ?? episodePreviewSubtitlesWebvtt,
            subtitles: result.subtitles ?? episodePreviewSubtitles,
          });
        }
        return;
      }
      if (!episodeUpscaledUrl) return;
      openWatch({
        url: episodeUpscaledUrl,
        subtitles_webvtt: episodePreviewSubtitlesWebvtt,
        subtitles: episodePreviewSubtitles,
      });
    } finally {
      setWatchUpscaledBusy(false);
    }
  };

  const captionsSrc = useMemo(
    () =>
      watching?.subtitles_webvtt
        ? `data:text/vtt;charset=utf-8,${encodeURIComponent(watching.subtitles_webvtt)}`
        : undefined,
    [watching?.subtitles_webvtt],
  );

  const activeCue = watching?.subtitles?.find(
    (cue) =>
      cue.start_microseconds <= positionMicroseconds &&
      positionMicroseconds < cue.start_microseconds + cue.duration_microseconds,
  );

  const upscaleDisabled = !canUpscale || anyBusy;
  const watchDisabled = !canWatch || anyBusy;
  const watchUpscaledDisabled = !canWatchUpscaled || anyBusy;

  const btnClass =
    "focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] px-2.5 py-1.5 text-[11.5px] text-[var(--color-text-2)] transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60";

  if (!onComposePreview && !onWatchPreview && !onUpscalePreview && !onWatchUpscaled) {
    return null;
  }

  return (
    <>
      <div className="flex shrink-0 flex-wrap items-stretch gap-3">
        {onComposePreview || onWatchPreview ? (
          <div className="flex flex-wrap items-center gap-1.5 self-center">
            {onComposePreview ? (
              <button
                type="button"
                onClick={() => void handleCompose()}
                disabled={!canCompose || anyBusy}
                title={
                  canCompose
                    ? t("reference_episode_compose_preview")
                    : t("reference_episode_compose_preview_disabled")
                }
                className={btnClass}
              >
                {composeBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Clapperboard className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span>
                  {composeBusy
                    ? t("reference_episode_compose_preview_busy")
                    : t("reference_episode_compose_preview")}
                </span>
              </button>
            ) : null}
            {onWatchPreview || onComposePreview ? (
              <button
                type="button"
                onClick={() => void handleWatch()}
                disabled={watchDisabled}
                title={
                  canWatch
                    ? t("reference_episode_watch_preview")
                    : t("reference_episode_watch_preview_disabled")
                }
                className={btnClass}
              >
                {watchBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Play className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span>
                  {watchBusy
                    ? t("reference_episode_watch_preview_busy")
                    : t("reference_episode_watch_preview")}
                </span>
              </button>
            ) : null}
          </div>
        ) : null}

        {(onUpscalePreview || onWatchUpscaled) && (
          <div className="flex flex-wrap items-center gap-1.5 self-center border-l border-[var(--color-hairline)] pl-3">
            {onUpscalePreview ? (
              <button
                type="button"
                onClick={() => void handleUpscale()}
                disabled={upscaleDisabled}
                title={
                  canUpscale
                    ? t("reference_episode_upscale_preview")
                    : t("reference_episode_upscale_preview_disabled")
                }
                className="focus-ring inline-flex items-center gap-2 rounded-md border border-[var(--color-accent)]/55 bg-[var(--color-accent-dim)] px-3.5 py-2 text-[12.5px] font-semibold text-[var(--color-accent-2)] transition-colors hover:bg-[oklch(0.28_0.04_265_/_0.55)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {upscaleBusy ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Maximize2 className="h-4 w-4" aria-hidden="true" />
                )}
                <span>{upscaleBusy ? upscaleBusyLabel : t("reference_episode_upscale_preview")}</span>
              </button>
            ) : null}
            {onWatchUpscaled ? (
              <button
                type="button"
                onClick={() => void handleWatchUpscaled()}
                disabled={watchUpscaledDisabled}
                title={
                  canWatchUpscaled
                    ? t("reference_episode_watch_upscaled")
                    : t("reference_episode_watch_upscaled_disabled")
                }
                className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-accent)]/40 bg-[oklch(0.24_0.03_265_/_0.45)] px-3 py-1.5 text-[12px] font-medium text-[var(--color-accent-2)] transition-colors hover:bg-[oklch(0.28_0.04_265_/_0.55)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {watchUpscaledBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Play className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span>
                  {watchUpscaledBusy
                    ? t("reference_episode_watch_upscaled_busy")
                    : t("reference_episode_watch_upscaled")}
                </span>
              </button>
            ) : null}
          </div>
        )}
      </div>

      {watching
        ? createPortal(
            <div
              role="dialog"
              aria-modal="true"
              aria-label={t("reference_episode_preview_dialog_title")}
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
              onClick={() => setWatching(null)}
            >
              <div
                className="flex w-full max-w-4xl flex-col gap-3 rounded-lg border border-[var(--color-hairline)] bg-[oklch(0.18_0.012_265)] p-4 shadow-xl"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center justify-between gap-3">
                  <h2 className="m-0 text-sm font-medium text-[var(--color-text)]">
                    {t("reference_episode_preview_dialog_title")}
                  </h2>
                  <div className="flex items-center gap-2">
                    {onUpscalePreview ? (
                      <button
                        type="button"
                        onClick={() => void handleUpscale()}
                        disabled={upscaleDisabled}
                        className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-2)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {upscaleBusy ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                        ) : (
                          <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        {upscaleBusy ? upscaleBusyLabel : t("reference_episode_upscale_preview")}
                      </button>
                    ) : null}
                    <a
                      href={watching.url}
                      download={`episode_${episode}_preview.mp4`}
                      className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-2)] hover:text-[var(--color-text)]"
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden="true" />
                      {t("reference_episode_preview_download")}
                    </a>
                    <button
                      type="button"
                      onClick={() => setWatching(null)}
                      className="focus-ring rounded-md p-1 text-[var(--color-text-3)] hover:text-[var(--color-text)]"
                      aria-label={t("reference_episode_preview_close")}
                    >
                      <X className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </div>
                </div>
                <div className="relative">
                  <video
                    key={watching.url}
                    src={watching.url}
                    controls
                    autoPlay
                    className="max-h-[70vh] w-full rounded bg-black"
                    onTimeUpdate={(event) => {
                      setPositionMicroseconds(
                        Math.round(event.currentTarget.currentTime * 1_000_000),
                      );
                    }}
                  >
                    {captionsSrc ? (
                      <track
                        kind="captions"
                        src={captionsSrc}
                        srcLang="und"
                        label={t("presentation_captions")}
                      />
                    ) : null}
                  </video>
                  {activeCue ? (
                    <div className="pointer-events-none absolute inset-x-3 bottom-14 flex justify-center">
                      <span className="max-w-[92%] rounded-md bg-black/75 px-2.5 py-1 text-center text-sm leading-relaxed text-white shadow-lg">
                        {activeCue.text}
                      </span>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
