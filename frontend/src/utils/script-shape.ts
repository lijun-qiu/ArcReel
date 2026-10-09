/**
 * 剧本形状分派（与后端 SCRIPT_SHAPES 同口径）：content_mode → 条目 ID / 角色引用字段。
 *
 * 时间线编辑器各组件（列表 / 分屏 / 详情 / 引用区）共用，避免三元映射在多处漂移。
 */

import type {
  AdEpisodeScript,
  AdShot,
  DramaEpisodeScript,
  DramaScene,
  EpisodeScript,
  NarrationEpisodeScript,
  NarrationSegment,
} from "@/types";

export type EditorContentMode = "narration" | "drama" | "ad";
export type ScriptItem = NarrationSegment | DramaScene | AdShot;

export type CharactersField =
  | "characters_in_segment"
  | "characters_in_scene"
  | "characters_in_shot";

/** 取条目 ID（segment_id / scene_id / shot_id）。 */
export function getScriptItemId(item: ScriptItem, mode: EditorContentMode): string {
  if (mode === "narration") return (item as NarrationSegment).segment_id;
  if (mode === "ad") return (item as AdShot).shot_id;
  return (item as DramaScene).scene_id;
}

/**
 * 取剧集剧本在该模式下的条目数组（segments / shots / scenes）。
 *
 * 未注册的模式返回空数组：以其他模式的形状渲染会让保存分派到错误端点。
 */
export function getScriptItems(
  script: EpisodeScript | null | undefined,
  mode: EditorContentMode,
): ScriptItem[] {
  if (!script) return [];
  if (mode === "narration") return (script as NarrationEpisodeScript).segments ?? [];
  if (mode === "ad") return (script as AdEpisodeScript).shots ?? [];
  if (mode === "drama") return (script as DramaEpisodeScript).scenes ?? [];
  return [];
}

/** 取该模式下条目的角色引用字段名。 */
export function charactersFieldFor(mode: EditorContentMode): CharactersField {
  if (mode === "drama") return "characters_in_scene";
  if (mode === "ad") return "characters_in_shot";
  return "characters_in_segment";
}

/** 上一镜分镜衔接参考（与后端 resolve_previous_storyboard_path 几何规则同口径）。 */
export interface PreviousStoryboardRef {
  id: string;
  path: string;
}

/**
 * 解析当前镜头可用的上一镜分镜图路径。
 *
 * 首镜 / 当前镜 segment_break / 上一镜无 storyboard_image → null。
 * 不读 use_previous_storyboard（那是「是否注入」开关，与「是否存在可展示的上一镜」分离）。
 */
export function resolvePreviousStoryboardRef(
  items: readonly ScriptItem[],
  mode: EditorContentMode,
  resourceId: string,
): PreviousStoryboardRef | null {
  const index = items.findIndex((item) => getScriptItemId(item, mode) === resourceId);
  if (index <= 0) return null;
  const target = items[index];
  if (!target || Boolean((target as { segment_break?: boolean }).segment_break)) return null;
  const previous = items[index - 1];
  if (!previous) return null;
  const previousId = getScriptItemId(previous, mode).trim();
  const path = previous.generated_assets?.storyboard_image;
  if (!previousId || !path) return null;
  return { id: previousId, path };
}

/** 缺省 false：存量无字段等同关闭；仅显式 true 时启用。 */
export function itemUsesPreviousStoryboard(item: {
  use_previous_storyboard?: boolean;
}): boolean {
  return item.use_previous_storyboard === true;
}

/** 时间线批量入口：全部重做 vs 只补缺。 */
export type TimelineBatchSelection = "all" | "missing";

/** 时间线批量入口：分镜图或成片。 */
export type TimelineBatchKind = "storyboard" | "video";

/**
 * 选出本集时间线批量入队目标（剧本顺序）。
 *
 * - 分镜图：``all`` 全选；``missing`` 仅尚无 ``storyboard_image`` 的镜头。
 * - 视频：必须已有分镜图；``all`` 含已有成片；``missing`` 仅尚无 ``video_clip``。
 */
export function selectTimelineBatchTargets(
  script: EpisodeScript | null | undefined,
  mode: EditorContentMode,
  kind: TimelineBatchKind,
  selection: TimelineBatchSelection,
): string[] {
  const items = getScriptItems(script, mode);
  const targets: string[] = [];
  for (const item of items) {
    const id = getScriptItemId(item, mode).trim();
    if (!id) continue;
    const hasStoryboard = Boolean(item.generated_assets?.storyboard_image);
    const hasVideo = Boolean(item.generated_assets?.video_clip);
    if (kind === "storyboard") {
      if (selection === "missing" && hasStoryboard) continue;
      targets.push(id);
      continue;
    }
    if (!hasStoryboard) continue;
    if (selection === "missing" && hasVideo) continue;
    targets.push(id);
  }
  return targets;
}

/**
 * 集总时长 = 逐条目 `duration_seconds` 求和。
 *
 * 它是派生值，剧本不落盘一份（服务端由项目摘要按同一口径读时计算）。各画布共用这一份
 * 实现，避免同一个数在时间线与宫格两处各写一遍、健壮度还不一致。
 */
export function sumItemDuration(
  items: readonly { duration_seconds?: number | null }[],
): number {
  return items.reduce((sum, item) => {
    const d = item.duration_seconds;
    return sum + (typeof d === "number" && Number.isFinite(d) ? d : 0);
  }, 0);
}
