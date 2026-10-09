import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Edit3, Film, MapPin, Plus, Puzzle, User } from "lucide-react";
import { API } from "@/api";
import { AvatarStack } from "@/components/ui/AvatarStack";
import { ClueStack } from "@/components/ui/ClueStack";
import {
  SegmentRefsEditModal,
  type SegmentRefsChanges,
} from "@/components/ui/SegmentRefsEditModal";
import { useProjectsStore } from "@/stores/projects-store";
import {
  charactersFieldFor,
  type EditorContentMode,
  type PreviousStoryboardRef,
} from "@/utils/script-shape";
import { WARM_TONE } from "@/utils/severity-tone";

interface ReferencesSectionProps {
  projectName: string;
  contentMode: EditorContentMode;
  characterNames: string[];
  sceneNames: string[];
  propNames: string[];
  onSave: (patch: Record<string, string[]>) => void | Promise<void>;
  disabled?: boolean;
  disabledHint?: string;
  /** Available previous-shot storyboard (geometry rules already met); omit group when null. */
  previousStoryboard?: PreviousStoryboardRef | null;
  /** Whether to inject previous shot as continuity reference; default true. */
  usePreviousStoryboard?: boolean;
  /** Toggle previous-shot injection; omit for read-only (no exclude/restore). */
  onTogglePreviousStoryboard?: (enabled: boolean) => void | Promise<void>;
}

const EMPTY_DICT = Object.freeze({});

function countMissing(names: string[], dict: Record<string, unknown>): number {
  let n = 0;
  for (const name of names) if (!Object.hasOwn(dict, name)) n += 1;
  return n;
}

export function ReferencesSection({
  projectName,
  contentMode,
  characterNames,
  sceneNames,
  propNames,
  onSave,
  disabled,
  disabledHint,
  previousStoryboard = null,
  usePreviousStoryboard = true,
  onTogglePreviousStoryboard,
}: ReferencesSectionProps) {
  const { t } = useTranslation("dashboard");
  const project = useProjectsStore((s) => s.currentProjectData);
  // Stabilize `?? {}` fallback so hook deps do not churn every render.
  const characters = useMemo(() => project?.characters ?? EMPTY_DICT, [project]);
  const scenes = useMemo(() => project?.scenes ?? EMPTY_DICT, [project]);
  const props = useMemo(() => project?.props ?? EMPTY_DICT, [project]);
  const [open, setOpen] = useState(false);
  const [togglingPrevious, setTogglingPrevious] = useState(false);

  const charField = charactersFieldFor(contentMode);

  const totalCount = characterNames.length + sceneNames.length + propNames.length;
  const hasPrevious = previousStoryboard != null;
  const isEmpty = totalCount === 0 && !hasPrevious;

  const totalStale = useMemo(() => {
    // Empty dicts while project is loading would mark every name stale; skip until loaded.
    if (!project) return 0;
    return (
      countMissing(characterNames, characters) +
      countMissing(sceneNames, scenes) +
      countMissing(propNames, props)
    );
  }, [project, characterNames, sceneNames, propNames, characters, scenes, props]);

  const [saving, setSaving] = useState(false);

  const handleSave = async (changes: SegmentRefsChanges) => {
    const patch: Record<string, string[]> = {};
    if (changes.characters !== undefined) patch[charField] = changes.characters;
    if (changes.scenes !== undefined) patch.scenes = changes.scenes;
    if (changes.props !== undefined) patch.props = changes.props;
    if (Object.keys(patch).length === 0) {
      setOpen(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(patch);
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const openModal = () => {
    if (disabled) return;
    setOpen(true);
  };

  const handleTogglePrevious = async () => {
    if (!onTogglePreviousStoryboard || togglingPrevious || disabled) return;
    setTogglingPrevious(true);
    try {
      await onTogglePreviousStoryboard(!usePreviousStoryboard);
    } finally {
      setTogglingPrevious(false);
    }
  };

  const eyebrow = (
    <div
      className="text-[10.5px] font-bold uppercase"
      style={{
        color: "var(--color-text-4)",
        letterSpacing: "1px",
        fontFamily: "var(--font-mono)",
      }}
    >
      {t("eyebrow_segment_refs")}
    </div>
  );

  const modal = open ? (
    <SegmentRefsEditModal
      open={open}
      onClose={() => setOpen(false)}
      onSave={handleSave}
      saving={saving}
      initialCharacters={characterNames}
      initialScenes={sceneNames}
      initialProps={propNames}
      characters={characters}
      scenes={scenes}
      props={props}
      projectName={projectName}
    />
  ) : null;

  const previousGroup =
    hasPrevious && previousStoryboard ? (
      <PreviousStoryboardGroup
        projectName={projectName}
        previous={previousStoryboard}
        enabled={usePreviousStoryboard}
        canToggle={Boolean(onTogglePreviousStoryboard) && !disabled}
        toggling={togglingPrevious}
        disabledHint={disabledHint}
        onToggle={() => void handleTogglePrevious()}
      />
    ) : null;

  if (isEmpty) {
    return (
      <div>
        <div className="mb-2 flex items-center justify-between">{eyebrow}</div>
        <button
          type="button"
          onClick={openModal}
          disabled={disabled}
          title={disabled ? disabledHint : t("references_add_cta")}
          className="focus-ring group flex w-full items-center gap-2.5 rounded-md px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            border: "1px dashed var(--color-hairline)",
            color: "var(--color-text-4)",
            background: "transparent",
          }}
          onMouseEnter={(e) => {
            if (disabled) return;
            e.currentTarget.style.borderColor = "var(--color-hairline-strong)";
            e.currentTarget.style.borderStyle = "solid";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = "var(--color-hairline)";
            e.currentTarget.style.borderStyle = "dashed";
          }}
        >
          <span className="flex-1 truncate text-[12px]">
            {t("references_empty_full")}
          </span>
          <span
            className="num inline-flex shrink-0 items-center gap-1 text-[11px]"
            style={{ color: "var(--color-accent-2)" }}
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            <span>{t("references_add_cta")}</span>
          </span>
        </button>
        {modal}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        {eyebrow}
        {totalStale > 0 && (
          <span
            className="num inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px]"
            style={{
              background: WARM_TONE.soft,
              border: `1px solid ${WARM_TONE.ring}`,
              color: WARM_TONE.color,
            }}
            title={t("segment_refs_stale_hint")}
          >
            <span aria-hidden="true">{"\u26A0"}</span>
            <span>{t("segment_refs_stale_badge", { count: totalStale })}</span>
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={openModal}
          disabled={disabled}
          title={disabled ? disabledHint : t("segment_refs_edit_button")}
          aria-label={t("segment_refs_edit_button")}
          className="focus-ring inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            color: "var(--color-text-3)",
            background: "transparent",
          }}
          onMouseEnter={(e) => {
            if (disabled) return;
            e.currentTarget.style.color = "var(--color-accent-2)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-3)";
          }}
        >
          <Edit3 className="h-3 w-3" aria-hidden="true" />
        </button>
      </div>

      <div
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md px-3 py-2"
        style={{
          border: "1px solid var(--color-hairline-soft)",
          background: "oklch(0.20 0.011 265 / 0.4)",
        }}
      >
        {characterNames.length > 0 && (
          <button
            type="button"
            onClick={openModal}
            disabled={disabled}
            title={disabled ? disabledHint : t("segment_refs_edit_button")}
            className="focus-ring group rounded-md text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Group
              icon={<User className="h-3 w-3" aria-hidden="true" />}
              label={t("references_badge_character")}
              count={characterNames.length}
            >
              <AvatarStack
                names={characterNames}
                characters={characters}
                projectName={projectName}
                maxShow={4}
              />
            </Group>
          </button>
        )}
        {sceneNames.length > 0 && (
          <button
            type="button"
            onClick={openModal}
            disabled={disabled}
            title={disabled ? disabledHint : t("segment_refs_edit_button")}
            className="focus-ring group rounded-md text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Group
              icon={<MapPin className="h-3 w-3" aria-hidden="true" />}
              label={t("references_badge_scene")}
              count={sceneNames.length}
            >
              <ClueStack
                sceneNames={sceneNames}
                propNames={[]}
                scenes={scenes}
                props={props}
                projectName={projectName}
                maxShow={4}
              />
            </Group>
          </button>
        )}
        {propNames.length > 0 && (
          <button
            type="button"
            onClick={openModal}
            disabled={disabled}
            title={disabled ? disabledHint : t("segment_refs_edit_button")}
            className="focus-ring group rounded-md text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Group
              icon={<Puzzle className="h-3 w-3" aria-hidden="true" />}
              label={t("references_badge_prop")}
              count={propNames.length}
            >
              <ClueStack
                sceneNames={[]}
                propNames={propNames}
                scenes={scenes}
                props={props}
                projectName={projectName}
                maxShow={4}
              />
            </Group>
          </button>
        )}
        {previousGroup}
        {totalCount === 0 && hasPrevious ? (
          <button
            type="button"
            onClick={openModal}
            disabled={disabled}
            title={disabled ? disabledHint : t("references_add_cta")}
            className="focus-ring inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            style={{ color: "var(--color-accent-2)" }}
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            <span>{t("references_add_cta")}</span>
          </button>
        ) : null}
      </div>

      {modal}
    </div>
  );
}

function PreviousStoryboardGroup({
  projectName,
  previous,
  enabled,
  canToggle,
  toggling,
  disabledHint,
  onToggle,
}: {
  projectName: string;
  previous: PreviousStoryboardRef;
  enabled: boolean;
  canToggle: boolean;
  toggling: boolean;
  disabledHint?: string;
  onToggle: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const fp = useProjectsStore((s) => s.getAssetFingerprint(previous.path));
  const url = API.getFileUrl(projectName, previous.path, fp);
  const toggleLabel = enabled
    ? t("references_previous_exclude")
    : t("references_previous_restore");
  const title = enabled
    ? t("references_previous_hint", { id: previous.id })
    : t("references_previous_excluded_hint", { id: previous.id });

  return (
    <div
      className="flex items-center gap-2"
      style={{ opacity: enabled ? 1 : 0.55 }}
      title={title}
    >
      <div
        className="h-7 w-7 overflow-hidden rounded"
        style={{
          border: enabled
            ? "1px solid var(--color-hairline-soft)"
            : "1px dashed var(--color-hairline)",
        }}
      >
        <img
          src={url}
          alt={t("references_previous_alt", { id: previous.id })}
          className="h-full w-full object-cover"
        />
      </div>
      <span
        className="inline-flex items-center gap-1 text-[11px]"
        style={{ color: "var(--color-text-3)" }}
      >
        <span style={{ color: "var(--color-text-4)" }}>
          <Film className="h-3 w-3" aria-hidden="true" />
        </span>
        <span>{t("references_badge_previous")}</span>
        <span className="num" style={{ color: "var(--color-text-2)" }}>
          {previous.id}
        </span>
        {!enabled ? (
          <span style={{ color: "var(--color-text-4)" }}>
            {t("references_previous_excluded_badge")}
          </span>
        ) : null}
      </span>
      {canToggle ? (
        <button
          type="button"
          onClick={onToggle}
          disabled={toggling}
          title={disabledHint ?? toggleLabel}
          aria-label={toggleLabel}
          aria-pressed={enabled}
          className="focus-ring rounded px-1.5 py-0.5 text-[10.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            color: enabled ? "var(--color-text-3)" : "var(--color-accent-2)",
            border: "1px solid var(--color-hairline-soft)",
          }}
        >
          {toggleLabel}
        </button>
      ) : null}
    </div>
  );
}

function Group({
  icon,
  label,
  count,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      {children}
      <span
        className="inline-flex items-center gap-1 text-[11px]"
        style={{ color: "var(--color-text-3)" }}
      >
        <span style={{ color: "var(--color-text-4)" }}>{icon}</span>
        <span>{label}</span>
        <span className="num" style={{ color: "var(--color-text-2)" }}>
          {count}
        </span>
      </span>
    </div>
  );
}
