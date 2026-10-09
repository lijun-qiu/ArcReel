import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import { errMsg } from "@/utils/async";
import type { PlanningSplitMode } from "@/types/project";

/** 与 lib.episode_planner 内部默认对齐；项目未写入字段时按此展示。 */
export const DEFAULT_PLANNING_WINDOW_CHARS = 20000;
export const DEFAULT_PLANNING_MAX_EPISODES = 20;

const WINDOW_PRESETS = [8000, 12000, 20000, 30000, 50000, 80000] as const;
const BATCH_PRESETS = [5, 10, 15, 20, 30, 40] as const;

function resolvePositiveInt(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : fallback;
}

function resolveSplitMode(value: unknown): PlanningSplitMode {
  return value === "markers" ? "markers" : "llm";
}

/**
 * 顶栏分集规划参数：切分模式 / 窗口字数 / 每批集数。
 * 「按标题标记」模式下提供一键规则切；未写入时显示内部默认。
 */
export function EpisodePlanningControls() {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const demoMode = useDemoWorkbench();
  const {
    currentProjectName,
    currentProjectData,
    currentScripts,
    setCurrentProject,
  } = useProjectsStore();

  const [busy, setBusy] = useState(false);

  if (!currentProjectName || !currentProjectData) return null;
  if (demoMode || isDemoProject(currentProjectName)) return null;
  if (currentProjectData.content_mode === "ad") return null;

  const windowChars = resolvePositiveInt(
    currentProjectData.planning_window_chars,
    DEFAULT_PLANNING_WINDOW_CHARS,
  );
  const maxEpisodes = resolvePositiveInt(
    currentProjectData.planning_max_episodes,
    DEFAULT_PLANNING_MAX_EPISODES,
  );
  const splitMode = resolveSplitMode(currentProjectData.planning_split_mode);
  const autoConfirmStep1 = currentProjectData.auto_confirm_step1 === true;
  const episodeCount = Array.isArray(currentProjectData.episodes)
    ? currentProjectData.episodes.length
    : 0;

  const windowOptions = WINDOW_PRESETS.includes(windowChars as (typeof WINDOW_PRESETS)[number])
    ? WINDOW_PRESETS
    : ([...WINDOW_PRESETS, windowChars] as number[]);
  const batchOptions = BATCH_PRESETS.includes(maxEpisodes as (typeof BATCH_PRESETS)[number])
    ? BATCH_PRESETS
    : ([...BATCH_PRESETS, maxEpisodes] as number[]);

  const persist = async (updates: {
    planning_window_chars?: number | null;
    planning_max_episodes?: number | null;
    planning_split_mode?: PlanningSplitMode | null;
    auto_confirm_step1?: boolean | null;
  }) => {
    setBusy(true);
    try {
      const res = await API.updateProject(currentProjectName, updates);
      setCurrentProject(currentProjectName, res.project, currentScripts);
      pushToast(t("planning_settings_saved"), "success");
    } catch (e) {
      pushToast(t("planning_settings_save_failed", { message: errMsg(e) }), "error");
    } finally {
      setBusy(false);
    }
  };

  const runMarkerSplit = async () => {
    if (episodeCount > 0) {
      const ok = window.confirm(t("planning_split_overwrite_confirm"));
      if (!ok) return;
    }
    setBusy(true);
    try {
      const res = await API.planEpisodesByMarkers(currentProjectName, {
        overwrite: episodeCount > 0,
      });
      setCurrentProject(currentProjectName, res.project, currentScripts);
      pushToast(t("planning_split_run_success", { count: res.total_planned }), "success");
    } catch (e) {
      pushToast(t("planning_split_run_failed", { message: errMsg(e) }), "error");
    } finally {
      setBusy(false);
    }
  };

  const selectStyle = {
    background: "oklch(0.22 0.011 265 / 0.5)",
    border: "1px solid var(--color-hairline-soft)",
    color: "var(--color-text-2)",
  } as const;

  return (
    <div
      className="flex items-center gap-1.5"
      title={t("planning_settings_hint")}
    >
      <label className="inline-flex h-[30px] items-center gap-1 rounded-md px-1.5 text-[11px]">
        <span style={{ color: "var(--color-text-3)" }}>{t("planning_split_mode_label")}</span>
        <select
          className="h-[26px] max-w-[6.5rem] rounded-md px-1 text-[11px] focus-ring"
          style={selectStyle}
          value={splitMode}
          disabled={busy}
          aria-label={t("planning_split_mode_label")}
          onChange={(e) => {
            const next = e.target.value === "markers" ? "markers" : "llm";
            if (next === splitMode) return;
            void persist({ planning_split_mode: next });
          }}
        >
          <option value="llm">{t("planning_split_mode_llm")}</option>
          <option value="markers">{t("planning_split_mode_markers")}</option>
        </select>
      </label>
      {splitMode === "markers" ? (
        <button
          type="button"
          className="inline-flex h-[30px] items-center rounded-md px-2 text-[11px] focus-ring"
          style={{
            ...selectStyle,
            cursor: busy ? "wait" : "pointer",
          }}
          disabled={busy}
          onClick={() => {
            void runMarkerSplit();
          }}
        >
          {t("planning_split_run")}
        </button>
      ) : (
        <>
          <label className="inline-flex h-[30px] items-center gap-1 rounded-md px-1.5 text-[11px]">
            <span style={{ color: "var(--color-text-3)" }}>{t("planning_window_label")}</span>
            <select
              className="h-[26px] max-w-[5.5rem] rounded-md px-1 text-[11px] focus-ring"
              style={selectStyle}
              value={windowChars}
              disabled={busy}
              aria-label={t("planning_window_label")}
              onChange={(e) => {
                const next = Number(e.target.value);
                if (!Number.isFinite(next) || next === windowChars) return;
                void persist({ planning_window_chars: next });
              }}
            >
              {windowOptions.map((n) => (
                <option key={n} value={n}>
                  {t("planning_window_option", { count: n })}
                </option>
              ))}
            </select>
          </label>
          <label className="inline-flex h-[30px] items-center gap-1 rounded-md px-1.5 text-[11px]">
            <span style={{ color: "var(--color-text-3)" }}>{t("planning_batch_label")}</span>
            <select
              className="h-[26px] max-w-[4.5rem] rounded-md px-1 text-[11px] focus-ring"
              style={selectStyle}
              value={maxEpisodes}
              disabled={busy}
              aria-label={t("planning_batch_label")}
              onChange={(e) => {
                const next = Number(e.target.value);
                if (!Number.isFinite(next) || next === maxEpisodes) return;
                void persist({ planning_max_episodes: next });
              }}
            >
              {batchOptions.map((n) => (
                <option key={n} value={n}>
                  {t("planning_batch_option", { count: n })}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: "var(--color-text-3)" }} /> : null}
      <label
        className="inline-flex h-[30px] items-center gap-1.5 rounded-md px-1.5 text-[11px]"
        title={t("auto_confirm_step1_hint")}
      >
        <input
          type="checkbox"
          className="h-3.5 w-3.5 accent-[var(--color-accent)]"
          checked={autoConfirmStep1}
          disabled={busy}
          aria-label={t("auto_confirm_step1_label")}
          onChange={(e) => {
            const next = e.target.checked;
            if (next === autoConfirmStep1) return;
            void persist({ auto_confirm_step1: next });
          }}
        />
        <span style={{ color: "var(--color-text-3)" }}>{t("auto_confirm_step1_label")}</span>
      </label>
    </div>
  );
}
