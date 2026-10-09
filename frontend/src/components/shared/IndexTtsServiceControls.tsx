import { useCallback, useEffect, useState } from "react";
import { Loader2, Power, PowerOff } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { IndexTtsServiceStatus } from "@/types";

interface Props {
  /** Compact toolbar styling for the reference-video canvas header. */
  compact?: boolean;
}

/**
 * Host IndexTTS2 start / unload controls.
 * Talks to ArcReel → host control agent (:10004) → speech proxy (:10003).
 */
export function IndexTtsServiceControls({ compact = false }: Props) {
  const { t } = useTranslation("dashboard");
  const [status, setStatus] = useState<IndexTtsServiceStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [unloading, setUnloading] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await API.getIndexTtsStatus();
      setStatus(next);
    } catch (e) {
      setStatus({
        ok: false,
        running: false,
        available: false,
        control_reachable: false,
        control_base: "",
        error: errMsg(e),
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const onStart = useCallback(async () => {
    if (starting) return;
    setStarting(true);
    try {
      useAppStore.getState().pushToast(t("indextts_starting_toast"), "info");
      const next = await API.startIndexTts();
      setStatus(next);
      if (next.running && next.ok !== false) {
        useAppStore.getState().pushToast(t("indextts_started_toast"), "success");
      } else {
        useAppStore.getState().pushToast(
          next.error || next.detail || t("indextts_start_failed_toast"),
          "error",
        );
      }
    } catch (e) {
      useAppStore.getState().pushToast(
        t("indextts_start_failed_toast_detail", { message: errMsg(e) }),
        "error",
      );
      await refresh();
    } finally {
      setStarting(false);
    }
  }, [refresh, starting, t]);

  const onUnload = useCallback(async () => {
    if (unloading) return;
    setUnloading(true);
    try {
      const next = await API.unloadIndexTts();
      setStatus(next);
      const killed = next.killed ?? 0;
      useAppStore.getState().pushToast(
        killed > 0
          ? t("indextts_unloaded_toast", { count: killed })
          : t("indextts_unloaded_none_toast"),
        "success",
      );
    } catch (e) {
      useAppStore.getState().pushToast(
        t("indextts_unload_failed_toast", { message: errMsg(e) }),
        "error",
      );
      await refresh();
    } finally {
      setUnloading(false);
    }
  }, [refresh, t, unloading]);

  const running = Boolean(status?.running);
  const controlOk = status?.control_reachable !== false || status === null;
  const btnBase = compact
    ? "focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-2)] transition-colors hover:bg-[oklch(0.26_0.013_265_/_0.7)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
    : "focus-ring inline-flex items-center gap-1.5 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-2.5 py-1.5 text-[12px] text-text-2 transition-colors hover:bg-bg-grad-b/70 hover:text-text disabled:cursor-not-allowed disabled:opacity-60";

  return (
    <div
      className={`inline-flex items-center gap-1.5 ${compact ? "" : "mt-3"}`}
      role="group"
      aria-label={t("indextts_controls_label")}
    >
      <span
        className={`inline-flex items-center gap-1.5 ${
          compact ? "text-[11px] text-[var(--color-text-3)]" : "text-[11.5px] text-text-3"
        }`}
      >
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            running ? "bg-[var(--color-good)]" : "bg-[var(--color-text-4)]"
          }`}
          aria-hidden
        />
        {running ? t("indextts_status_ready") : t("indextts_status_stopped")}
      </span>
      <button
        type="button"
        className={btnBase}
        disabled={starting || unloading || !controlOk}
        title={
          controlOk
            ? t("indextts_start_hint")
            : status?.error || t("indextts_control_unreachable")
        }
        onClick={() => void onStart()}
      >
        {starting ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <Power className="h-3.5 w-3.5" aria-hidden />
        )}
        <span>{starting ? t("submitting") : t("indextts_start")}</span>
      </button>
      <button
        type="button"
        className={btnBase}
        disabled={unloading || starting || (!running && !status?.control_reachable)}
        title={t("indextts_unload_hint")}
        onClick={() => void onUnload()}
      >
        {unloading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        ) : (
          <PowerOff className="h-3.5 w-3.5" aria-hidden />
        )}
        <span>{unloading ? t("submitting") : t("indextts_unload")}</span>
      </button>
    </div>
  );
}
