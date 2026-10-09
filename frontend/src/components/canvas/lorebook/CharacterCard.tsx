import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Expand, ImagePlus, Pause, Play, Plus, Trash2, Upload, User, X } from "lucide-react";
import { enqueueCharacter } from "@/actions/generation";
import { API } from "@/api";
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
import { ImageEditButton } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { AspectFrame } from "@/components/ui/AspectFrame";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { GenerateButton } from "@/components/ui/GenerateButton";
import { GlassModal } from "@/components/ui/GlassModal";
import { ImageFlipReveal } from "@/components/ui/ImageFlipReveal";
import { PreviewableImageFrame } from "@/components/ui/PreviewableImageFrame";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import {
  previewCharacterLookPrompt,
  previewCharacterPrompt,
} from "@/utils/character-prompt-preview";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { EditableAssetName } from "./EditableAssetName";
import { VoiceSampleButton } from "./VoiceSampleButton";
import type { Character } from "@/types";

interface CharacterSavePayload {
  description: string;
  voiceStyle: string;
  referenceFile?: File | null;
  audioFile?: File | null;
}

/** mm:ss；无法确定时长（尚未加载完 metadata）时占位 --:--。 */
function formatAudioDuration(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

interface CharacterCardProps {
  name: string;
  character: Character;
  projectName: string;
  onSave: (name: string, payload: CharacterSavePayload) => Promise<void>;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => Promise<void> | void;
  onReload?: () => Promise<unknown> | void;
  generating?: boolean;
  /** 只读展示（引导演示项目）：所有改写入口不渲染，文本字段不可编辑。 */
  readOnly?: boolean;
}

const FIELD_STYLE: React.CSSProperties = {
  background:
    "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.6), oklch(0.18 0.010 265 / 0.45))",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
  boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.2)",
};

function WardrobeLookRow({
  projectName,
  characterName,
  lookId,
  description,
  sheetPath,
  fallbackDescription,
  projectStyle,
  projectStyleDescription,
  busy,
  readOnly,
  onBusy,
  onReload,
}: {
  projectName: string;
  characterName: string;
  lookId: string;
  description: string;
  sheetPath?: string;
  fallbackDescription: string;
  projectStyle: string;
  projectStyleDescription: string;
  busy: boolean;
  readOnly: boolean;
  onBusy: (busy: boolean) => void;
  onReload?: () => Promise<unknown> | void;
}) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const lookAssetId = `${characterName}__${lookId}`;
  const lookFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const lookUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, lookFp) : null;
  const busyIds = useActiveResourceIds("character", projectName);
  const lookBusy = busy || busyIds.has(lookAssetId);
  const label = `${characterName}@${lookId}`;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(description);
  const [savingLook, setSavingLook] = useState(false);
  const titleId = useId();

  useEffect(() => {
    if (open) setDraft(description);
  }, [open, description]);

  const lookDirty = draft !== description;
  const fullPrompt = previewCharacterLookPrompt(characterName, lookId, draft, {
    baseDescription: fallbackDescription,
    style: projectStyle,
    styleDescription: projectStyleDescription,
  });

  return (
    <>
      <li className="list-none">
        <button
          type="button"
          onClick={() => setOpen(true)}
          title={label}
          aria-label={t("wardrobe_look_modal_title", {
            ns: "assets",
            name: characterName,
            look: lookId,
          })}
          className="focus-ring group flex w-[4.5rem] flex-col items-stretch gap-1 rounded-lg p-0.5 text-left transition-colors hover:bg-[oklch(1_0_0_/_0.04)]"
        >
          <div
            className="aspect-square w-full overflow-hidden rounded-md"
            style={{
              background: "var(--color-bg-2)",
              border: lookBusy
                ? "1px solid var(--color-accent-soft)"
                : "1px solid var(--color-border)",
            }}
          >
            {lookUrl ? (
              <img src={lookUrl} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full items-center justify-center">
                <User className="h-5 w-5" style={{ color: "var(--color-text-4)" }} />
              </div>
            )}
          </div>
          <span
            className="truncate text-center text-[10px] leading-tight"
            style={{ color: "var(--color-text-3)" }}
          >
            {lookId}
          </span>
        </button>
      </li>

      <GlassModal
        open={open}
        onClose={() => {
          if (savingLook) return;
          setOpen(false);
        }}
        labelledBy={titleId}
        widthClassName="w-full max-w-lg"
        panelClassName="flex max-h-[85vh] flex-col overflow-hidden"
        closeOnEscape={!savingLook}
        closeOnBackdrop={!savingLook}
      >
        <div className="flex shrink-0 items-start gap-2 px-5 pb-3 pt-5">
          <h2
            id={titleId}
            className="min-w-0 flex-1 break-words text-[15px] font-semibold"
            style={{ color: "var(--color-text)" }}
          >
            {t("wardrobe_look_modal_title", { ns: "assets", name: characterName, look: lookId })}
          </h2>
          {readOnly ? null : (
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
              <button
                type="button"
                disabled={savingLook || !lookDirty}
                className="focus-ring rounded-md px-2.5 py-1.5 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
                style={{
                  color: "oklch(0.14 0 0)",
                  background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                }}
                onClick={async () => {
                  setSavingLook(true);
                  try {
                    await API.updateCharacterLook(projectName, characterName, lookId, draft);
                    useAppStore.getState().pushToast(t("wardrobe_look_saved", { ns: "assets" }), "success");
                    await onReload?.();
                  } catch (err) {
                    useAppStore.getState().pushToast(errMsg(err), "error");
                  } finally {
                    setSavingLook(false);
                  }
                }}
              >
                {savingLook ? t("common:saving") : t("wardrobe_save_look", { ns: "assets" })}
              </button>
              <button
                type="button"
                disabled={lookBusy || savingLook}
                className="focus-ring rounded-md px-2.5 py-1.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
                style={{
                  background: "var(--color-accent-dim)",
                  border: "1px solid var(--color-accent-soft)",
                  color: "var(--color-accent-2)",
                }}
                onClick={async () => {
                  if (rejectIfAssetBusy("character", projectName, lookAssetId, t)) return;
                  onBusy(true);
                  try {
                    await enqueueCharacter(
                      projectName,
                      lookAssetId,
                      draft.trim() || description || fallbackDescription,
                    );
                    await onReload?.();
                  } catch (err) {
                    useAppStore.getState().pushToast(errMsg(err), "error");
                  } finally {
                    onBusy(false);
                  }
                }}
              >
                {t("wardrobe_generate", { ns: "assets" })}
              </button>
              <VersionTimeMachine
                projectName={projectName}
                resourceType="characters"
                resourceId={lookAssetId}
                onRestore={onReload ? async () => { await onReload(); } : undefined}
                iconOnly
                busy={lookBusy || savingLook}
              />
              <button
                type="button"
                disabled={busy || savingLook}
                title={t("wardrobe_delete", { ns: "assets" })}
                aria-label={t("wardrobe_delete", { ns: "assets" })}
                className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-warm-bright)] disabled:cursor-not-allowed disabled:opacity-40"
                style={{ color: "var(--color-text-3)" }}
                onClick={async () => {
                  onBusy(true);
                  try {
                    await API.deleteCharacterLook(projectName, characterName, lookId);
                    setOpen(false);
                    await onReload?.();
                  } catch (err) {
                    useAppStore.getState().pushToast(errMsg(err), "error");
                  } finally {
                    onBusy(false);
                  }
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          <button
            type="button"
            onClick={() => setOpen(false)}
            disabled={savingLook}
            className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
            aria-label={t("common:cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 pb-5">
          <PreviewableImageFrame src={lookUrl} alt={label} buttonClassName="right-2 top-2">
            <div
              className="overflow-hidden rounded-lg"
              style={{ border: "1px solid var(--color-hairline-soft)", background: "var(--color-bg-2)" }}
            >
              {lookUrl ? (
                <img src={lookUrl} alt={label} className="max-h-40 w-full object-contain sm:max-h-48" />
              ) : (
                <div className="flex h-28 items-center justify-center" style={{ color: "var(--color-text-4)" }}>
                  <User className="h-10 w-10" />
                </div>
              )}
            </div>
          </PreviewableImageFrame>

          <div>
            <CapsLabel>{t("wardrobe_look_desc", { ns: "assets" })}</CapsLabel>
            <textarea
              value={draft}
              readOnly={readOnly}
              onChange={(e) => setDraft(e.target.value)}
              rows={3}
              className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
              style={FIELD_STYLE}
              placeholder={t("wardrobe_look_desc", { ns: "assets" })}
            />
          </div>

          <div>
            <CapsLabel>{t("full_prompt_label", { ns: "assets" })}</CapsLabel>
            <pre
              className="mt-1.5 whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-[11px] leading-relaxed"
              style={{ ...FIELD_STYLE, color: "var(--color-text-2)" }}
            >
              {fullPrompt}
            </pre>
            <p className="mt-1 text-[10px]" style={{ color: "var(--color-text-4)" }}>
              {t("full_prompt_hint", { ns: "assets" })}
            </p>
          </div>
        </div>
      </GlassModal>
    </>
  );
}

export function CharacterCard({
  name,
  character,
  projectName,
  onSave,
  onGenerate,
  onRestoreVersion,
  onReload,
  generating = false,
  readOnly = false,
}: CharacterCardProps) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const sheetFp = useProjectsStore(
    (s) => character.character_sheet ? s.getAssetFingerprint(character.character_sheet) : null,
  );
  const referenceFp = useProjectsStore(
    (s) => character.reference_image ? s.getAssetFingerprint(character.reference_image) : null,
  );
  const audioFp = useProjectsStore(
    (s) => character.reference_audio ? s.getAssetFingerprint(character.reference_audio) : null,
  );
  const [description, setDescription] = useState(character.description);
  const [voiceStyle, setVoiceStyle] = useState(character.voice_style ?? "");
  const [newLookId, setNewLookId] = useState("");
  const [newLookDesc, setNewLookDesc] = useState("");
  const [wardrobeBusy, setWardrobeBusy] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioPreview, setAudioPreview] = useState<string | null>(null);
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [audioProgress, setAudioProgress] = useState(0);
  const [audioDuration, setAudioDuration] = useState<number | null>(null);
  const [deletingAudio, setDeletingAudio] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadingSheet, setUploadingSheet] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [descModalOpen, setDescModalOpen] = useState(false);
  const [descDraft, setDescDraft] = useState(character.description);
  const [addLookOpen, setAddLookOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const audioElRef = useRef<HTMLAudioElement>(null);
  const sheetInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const descId = useId();
  const voiceId = useId();
  const descModalTitleId = useId();
  const descModalFieldId = useId();
  const addLookTitleId = useId();
  const projectStyle = useProjectsStore((s) => s.currentProjectData?.style ?? "");
  const projectStyleDescription = useProjectsStore(
    (s) => s.currentProjectData?.style_description ?? "",
  );

  const handleSheetUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (rejectIfAssetBusy("character", projectName, name, t)) return;
    setUploadingSheet(true);
    try {
      await API.uploadFile(projectName, "character", file, name);
      await onReload?.();
      useAppStore.getState().pushToast(t("assets:upload_sheet_success", { name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setUploadingSheet(false);
    }
  };

  useEffect(() => {
    // 上游角色变化时同步本地草稿字段
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDescription(character.description);
    setVoiceStyle(character.voice_style ?? "");
  }, [character.description, character.voice_style]);

  useEffect(() => {
    if (descModalOpen) setDescDraft(description);
  }, [descModalOpen, description]);

  useEffect(() => {
    // 角色立绘变化时重置图片加载错误标记
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setImgError(false);
  }, [character.character_sheet, sheetFp]);

  useEffect(() => {
    // 上游参考图变化时清空本地未提交的上传文件 + 释放 blob URL
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setReferenceFile(null);
    setReferencePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, [character.reference_image]);

  useEffect(() => {
    return () => {
      if (referencePreview) {
        URL.revokeObjectURL(referencePreview);
      }
    };
  }, [referencePreview]);

  useEffect(() => {
    // 上游参考音频变化时清空本地未提交的上传文件 + 释放 blob URL；
    // 同扩展名替换（如 wav 换 wav）路径字符串不变，靠 audioFp 才能感知内容已更新
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAudioFile(null);
    setAudioPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, [character.reference_audio, audioFp]);

  useEffect(() => {
    return () => {
      if (audioPreview) {
        URL.revokeObjectURL(audioPreview);
      }
    };
  }, [audioPreview]);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, []);

  useEffect(() => {
    if (descModalOpen) autoResize();
  }, [autoResize, descModalOpen, descDraft]);

  const isDirty =
    description !== character.description ||
    voiceStyle !== (character.voice_style ?? "") ||
    referenceFile !== null ||
    audioFile !== null;

  const handleReferenceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setReferenceFile(file);
    setReferencePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    e.target.value = "";
  };

  const clearPendingReference = () => {
    setReferenceFile(null);
    setReferencePreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleAudioChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setAudioFile(file);
    setAudioPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    e.target.value = "";
  };

  const clearPendingAudio = () => {
    setAudioFile(null);
    setAudioPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (audioInputRef.current) {
      audioInputRef.current.value = "";
    }
  };

  const handleDeleteAudio = async () => {
    if (audioFile) {
      clearPendingAudio();
      return;
    }
    if (!character.reference_audio) return;
    if (rejectIfAssetBusy("character", projectName, name, t, "assets:delete_audio_busy_hint")) return;
    setDeletingAudio(true);
    try {
      await API.deleteCharacterReferenceAudio(projectName, name);
      await onReload?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeletingAudio(false);
    }
  };

  const handleDelete = async () => {
    if (deleting) return;
    if (rejectIfAssetBusy("character", projectName, name, t, "assets:delete_busy_hint")) return;
    setDeleting(true);
    try {
      await API.deleteCharacter(projectName, name);
      setConfirmDelete(false);
      useAppStore.getState().pushToast(t("assets:delete_success", { name }), "success");
      await onReload?.();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeleting(false);
    }
  };

  const toggleAudioPlayback = () => {
    const el = audioElRef.current;
    if (!el) return;
    if (isAudioPlaying) {
      el.pause();
    } else {
      void el.play();
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(name, {
        description,
        voiceStyle,
        referenceFile,
        audioFile,
      });
    } finally {
      setSaving(false);
    }
  };

  const sheetUrl = character.character_sheet
    ? API.getFileUrl(projectName, character.character_sheet, sheetFp)
    : null;

  const savedReferenceUrl = character.reference_image
    ? API.getFileUrl(projectName, character.reference_image, referenceFp)
    : null;

  const displayedReferenceUrl = referencePreview ?? savedReferenceUrl;
  const hasSavedReference = Boolean(savedReferenceUrl) && !referencePreview;

  const savedAudioUrl = character.reference_audio
    ? API.getFileUrl(projectName, character.reference_audio, audioFp)
    : null;

  const displayedAudioUrl = audioPreview ?? savedAudioUrl;

  useEffect(() => {
    // 音源切换（更换/删除/上游变化）时复位播放状态，避免残留上一段的播放进度
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsAudioPlaying(false);
    setAudioProgress(0);
    setAudioDuration(null);
  }, [displayedAudioUrl]);

  return (
    <div
      id={`character-${name}`}
      className="relative overflow-hidden rounded-xl p-5"
      data-workspace-editing={isEditing || isDirty ? "true" : undefined}
      onFocusCapture={() => setIsEditing(true)}
      onBlurCapture={(event) => {
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
          return;
        }
        setIsEditing(false);
      }}
      style={{
        background:
          "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))",
        border: "1px solid var(--color-hairline-soft)",
        boxShadow:
          "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 12px 30px -12px oklch(0 0 0 / 0.4)",
      }}
    >
      {/* Top accent hairline */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-5 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      {/* ---- Header: 单排 icon + name + icon-only 工具栏 ---- */}
      <div className="mb-4 flex items-start gap-2.5">
        <span
          aria-hidden
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md"
          style={{
            background: "var(--color-accent-dim)",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
          }}
        >
          <User className="h-3.5 w-3.5" />
        </span>
        <EditableAssetName
          projectName={projectName}
          name={name}
          assetType="character"
          readOnly={readOnly}
          /* 改名会搬动落盘文件：与本卡片自身在途的写请求交错会留下旧名孤儿文件，
             因此改名比兄弟控件多禁用一档，把卡片本地的在途标志也算进占用态。 */
          busy={generating || uploadingSheet || saving || deletingAudio || deleting}
        />
        {readOnly ? null : (
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => sheetInputRef.current?.click()}
            disabled={uploadingSheet || generating || deleting}
            title={t("assets:upload_sheet")}
            aria-label={t("assets:upload_sheet")}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <Upload className="h-3.5 w-3.5" />
          </button>
          <input
            ref={sheetInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("assets:upload_sheet")}
            className="hidden"
            onChange={(e) => void handleSheetUpload(e)}
          />
          <ImageEditButton
            projectName={projectName}
            resourceType="character"
            resourceId={name}
            hasImage={Boolean(character.character_sheet)}
            busy={generating || uploadingSheet || deleting}
          />
          <AddToLibraryButton
            resourceType="character"
            resourceId={name}
            projectName={projectName}
            initialDescription={character.description}
            initialVoiceStyle={character.voice_style ?? ""}
            sheetPath={character.character_sheet}
            busy={generating || uploadingSheet || deleting}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
          />
          <VersionTimeMachine
            projectName={projectName}
            resourceType="characters"
            resourceId={name}
            onRestore={onRestoreVersion}
            iconOnly
            busy={generating || uploadingSheet || deleting}
          />
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            disabled={uploadingSheet || generating || deleting || deletingAudio}
            title={t("assets:delete")}
            aria-label={t("assets:delete")}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] hover:text-[var(--color-warm-bright)] disabled:cursor-not-allowed disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
        )}
      </div>

      {/* ---- Image area ---- */}
      <div className="mb-4 space-y-3">
        <div>
          <CapsLabel>{t("character_design")}</CapsLabel>
          <div
            className="mt-1.5 overflow-hidden rounded-lg"
            style={{ border: "1px solid var(--color-hairline-soft)" }}
          >
            <PreviewableImageFrame
              src={sheetUrl && !imgError ? sheetUrl : null}
              alt={`${name} ${t("character_design")}`}
            >
              <AspectFrame ratio="16:9">
                <ImageFlipReveal
                  src={sheetUrl && !imgError ? sheetUrl : null}
                  alt={`${name} ${t("character_design")}`}
                  className="h-full w-full object-contain"
                  onError={() => setImgError(true)}
                  fallback={
                    <div
                      className="flex h-full w-full flex-col items-center justify-center gap-2"
                      style={{ color: "var(--color-text-4)" }}
                    >
                      <User className="h-10 w-10" />
                      <span className="text-xs">{t("click_to_generate")}</span>
                    </div>
                  }
                />
              </AspectFrame>
            </PreviewableImageFrame>
          </div>
        </div>

        {/* 只读且没有参考图时整块不渲染：留一个不能用的上传框只是噪声 */}
        {readOnly && !displayedReferenceUrl ? null : (
        <div>
          <div className="flex items-center justify-between">
            <CapsLabel>{t("reference_image")}</CapsLabel>
            {!readOnly && (referenceFile || hasSavedReference) && (
              <button
                type="button"
                onClick={() =>
                  referenceFile
                    ? clearPendingReference()
                    : fileInputRef.current?.click()
                }
                className="focus-ring text-[11px] text-[var(--color-text-3)] transition-colors hover:text-[var(--color-text)]"
              >
                {referenceFile ? t("cancel_pending") : t("replace")}
              </button>
            )}
          </div>

          {displayedReferenceUrl ? (
            <PreviewableImageFrame
              src={displayedReferenceUrl}
              alt={`${name} ${t("reference_image")}`}
              buttonClassName="right-2.5 top-2.5"
            >
              <div
                className="relative mt-1.5 overflow-hidden rounded-lg"
                style={{ border: "1px solid var(--color-hairline-soft)" }}
              >
                <img
                  src={displayedReferenceUrl}
                  alt={`${name} ${t("reference_image")}`}
                  className="h-28 w-full object-cover"
                />
                <div
                  className="absolute inset-x-0 bottom-0 flex items-center justify-between px-3 py-2"
                  style={{
                    background:
                      "linear-gradient(180deg, transparent, oklch(0 0 0 / 0.65))",
                  }}
                >
                  <span
                    className="flex items-center gap-1.5 text-[11px]"
                    style={{ color: "var(--color-text)" }}
                  >
                    <ImagePlus className="h-3.5 w-3.5" />
                    {referenceFile ? t("unsaved_reference") : t("saved_reference")}
                  </span>
                  {readOnly ? null : (
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="focus-ring rounded px-2 py-0.5 text-[11px] transition-colors"
                    style={{
                      background: "oklch(0 0 0 / 0.5)",
                      color: "var(--color-text)",
                      border: "1px solid oklch(1 0 0 / 0.1)",
                    }}
                  >
                    {t("change")}
                  </button>
                  )}
                </div>
              </div>
            </PreviewableImageFrame>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="focus-ring mt-1.5 flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-4 text-sm text-[var(--color-text-4)] transition-colors hover:border-[var(--color-accent-soft)] hover:text-[var(--color-text-2)]"
              style={{ background: "oklch(0.18 0.010 265 / 0.35)" }}
            >
              <Upload className="h-4 w-4" />
              {t("upload_reference")}
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            aria-label={t("upload_character_ref_aria")}
            onChange={handleReferenceChange}
            className="hidden"
          />
        </div>
        )}
      </div>

      <div className="mt-1.5 flex items-center justify-between gap-2">
        <CapsLabel htmlFor={descId}>{t("description")}</CapsLabel>
        <button
          type="button"
          onClick={() => setDescModalOpen(true)}
          className="focus-ring inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
          style={{ color: "var(--color-accent-2)" }}
        >
          <Expand className="h-3 w-3" />
          {t("description_expand", { ns: "assets" })}
        </button>
      </div>
      <button
        type="button"
        id={descId}
        onClick={() => setDescModalOpen(true)}
        className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-left text-[13px] leading-[1.55] outline-none transition-[border-color,box-shadow]"
        style={FIELD_STYLE}
      >
        <span
          className="line-clamp-3 whitespace-pre-wrap break-words"
          style={{ color: description ? "var(--color-text)" : "var(--color-text-4)" }}
        >
          {description || t("character_desc_placeholder")}
        </span>
      </button>

      <GlassModal
        open={descModalOpen}
        onClose={() => setDescModalOpen(false)}
        labelledBy={descModalTitleId}
        widthClassName="w-full max-w-lg"
        panelClassName="flex max-h-[85vh] flex-col overflow-hidden"
      >
        <div className="flex shrink-0 items-start justify-between gap-3 px-5 pb-3 pt-5">
          <h2
            id={descModalTitleId}
            className="text-[15px] font-semibold"
            style={{ color: "var(--color-text)" }}
          >
            {t("description_modal_title", { ns: "assets" })}
          </h2>
          <button
            type="button"
            onClick={() => setDescModalOpen(false)}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md"
            style={{ color: "var(--color-text-3)" }}
            aria-label={t("common:cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 pb-3">
          <div>
            <CapsLabel htmlFor={descModalFieldId}>{t("description")}</CapsLabel>
            <textarea
              ref={textareaRef}
              id={descModalFieldId}
              value={descDraft}
              readOnly={readOnly}
              onChange={(e) => setDescDraft(e.target.value)}
              onInput={autoResize}
              rows={5}
              className="focus-ring mt-1.5 w-full resize-y rounded-lg px-3 py-2 text-[13px] leading-[1.55] outline-none"
              style={{ ...FIELD_STYLE, minHeight: "6rem" }}
              placeholder={t("character_desc_placeholder")}
            />
          </div>
          <div>
            <CapsLabel>{t("full_prompt_label", { ns: "assets" })}</CapsLabel>
            <pre
              className="mt-1.5 whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-[11px] leading-relaxed"
              style={{ ...FIELD_STYLE, color: "var(--color-text-2)" }}
            >
              {previewCharacterPrompt(name, descDraft, projectStyle, projectStyleDescription)}
            </pre>
            <p className="mt-1 text-[10px]" style={{ color: "var(--color-text-4)" }}>
              {t("full_prompt_hint", { ns: "assets" })}
            </p>
          </div>
        </div>
        {readOnly ? null : (
          <div
            className="flex shrink-0 border-t px-5 py-3"
            style={{ borderColor: "var(--color-hairline-soft)" }}
          >
            <button
              type="button"
              className="focus-ring rounded-lg px-3 py-2 text-[12px] font-medium"
              style={{
                color: "oklch(0.14 0 0)",
                background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
              }}
              onClick={() => {
                setDescription(descDraft);
                setDescModalOpen(false);
              }}
            >
              {t("description_modal_done", { ns: "assets" })}
            </button>
          </div>
        )}
      </GlassModal>

      <div className="mt-3">
        <CapsLabel>{t("wardrobe_title", { ns: "assets" })}</CapsLabel>
        <ul className="mt-2 flex flex-wrap gap-2">
          {Object.entries(
            character.looks ?? {
              base: {
                description: character.description,
                character_sheet: character.character_sheet,
              },
            },
          )
            .filter(([lookId]) => lookId !== "base")
            .map(([lookId, look]) => (
              <WardrobeLookRow
                key={lookId}
                projectName={projectName}
                characterName={name}
                lookId={lookId}
                description={look.description || ""}
                sheetPath={look.character_sheet}
                fallbackDescription={description}
                projectStyle={projectStyle}
                projectStyleDescription={projectStyleDescription}
                busy={wardrobeBusy}
                readOnly={readOnly}
                onBusy={setWardrobeBusy}
                onReload={onReload}
              />
            ))}
          {readOnly ? null : (
            <li className="list-none">
              <button
                type="button"
                onClick={() => setAddLookOpen(true)}
                disabled={wardrobeBusy}
                title={t("wardrobe_add", { ns: "assets" })}
                aria-label={t("wardrobe_add", { ns: "assets" })}
                className="focus-ring flex w-[4.5rem] flex-col items-stretch gap-1 rounded-lg p-0.5 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <div
                  className="flex aspect-square w-full items-center justify-center rounded-md border border-dashed"
                  style={{
                    borderColor: "var(--color-hairline)",
                    background: "oklch(0.18 0.010 265 / 0.35)",
                    color: "var(--color-text-4)",
                  }}
                >
                  <Plus className="h-5 w-5" />
                </div>
                <span className="truncate text-center text-[10px]" style={{ color: "var(--color-text-4)" }}>
                  {t("wardrobe_add", { ns: "assets" })}
                </span>
              </button>
            </li>
          )}
        </ul>
      </div>

      <GlassModal
        open={addLookOpen}
        onClose={() => {
          if (wardrobeBusy) return;
          setAddLookOpen(false);
        }}
        labelledBy={addLookTitleId}
        widthClassName="w-full max-w-md"
        closeOnEscape={!wardrobeBusy}
        closeOnBackdrop={!wardrobeBusy}
      >
        <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-5">
          <h2
            id={addLookTitleId}
            className="text-[15px] font-semibold"
            style={{ color: "var(--color-text)" }}
          >
            {t("wardrobe_add_modal_title", { ns: "assets" })}
          </h2>
          <button
            type="button"
            onClick={() => setAddLookOpen(false)}
            disabled={wardrobeBusy}
            className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md disabled:opacity-40"
            style={{ color: "var(--color-text-3)" }}
            aria-label={t("common:cancel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3 px-5 pb-5">
          <p className="text-[11px] leading-snug" style={{ color: "var(--color-text-4)" }}>
            {t("wardrobe_hint", { ns: "assets" })}
          </p>
          <input
            type="text"
            value={newLookId}
            onChange={(e) => setNewLookId(e.target.value)}
            placeholder={t("wardrobe_look_id", { ns: "assets" })}
            className="focus-ring w-full rounded-lg px-3 py-2 text-[13px] outline-none"
            style={FIELD_STYLE}
          />
          <textarea
            value={newLookDesc}
            onChange={(e) => setNewLookDesc(e.target.value)}
            placeholder={t("wardrobe_look_desc", { ns: "assets" })}
            rows={4}
            className="focus-ring w-full resize-y rounded-lg px-3 py-2 text-[13px] outline-none"
            style={FIELD_STYLE}
          />
          <button
            type="button"
            disabled={wardrobeBusy || !newLookId.trim()}
            className="focus-ring rounded-lg px-3 py-2 text-[12px] disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: "var(--color-accent-dim)",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
            }}
            onClick={async () => {
              setWardrobeBusy(true);
              try {
                await API.createCharacterLook(projectName, name, newLookId.trim(), newLookDesc.trim());
                setNewLookId("");
                setNewLookDesc("");
                setAddLookOpen(false);
                await onReload?.();
              } catch (err) {
                useAppStore.getState().pushToast(errMsg(err), "error");
              } finally {
                setWardrobeBusy(false);
              }
            }}
          >
            {t("wardrobe_add", { ns: "assets" })}
          </button>
        </div>
      </GlassModal>

      <div className="mt-3">
        {/* 「声音」是描述输入 + 音频样本共用的分组标题，不单独绑定输入框；
            输入框自带 aria-label 保留「声音风格」这一字段身份 */}
        <CapsLabel>{t("voice_section")}</CapsLabel>
        <input
          id={voiceId}
          type="text"
          value={voiceStyle}
          readOnly={readOnly}
          aria-label={t("voice_style")}
          onChange={(e) => setVoiceStyle(e.target.value)}
          className="focus-ring mt-1.5 w-full rounded-lg px-3 py-2 text-[13px] outline-none transition-[border-color,box-shadow]"
          style={FIELD_STYLE}
          placeholder={t("voice_style_example")}
        />

        {readOnly && !displayedAudioUrl ? null : (
        <div className="mt-1.5">
          {displayedAudioUrl ? (
            <div
              className="flex items-center gap-2 rounded-lg px-2.5 py-1.5"
              style={FIELD_STYLE}
            >
              <button
                type="button"
                onClick={toggleAudioPlayback}
                aria-label={isAudioPlaying ? t("pause_audio_sample") : t("play_audio_sample")}
                className="focus-ring grid h-7 w-7 shrink-0 place-items-center rounded-full transition-colors"
                style={{
                  background: "var(--color-accent-dim)",
                  border: "1px solid var(--color-accent-soft)",
                  color: "var(--color-accent-2)",
                }}
              >
                {isAudioPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 translate-x-px" />}
              </button>

              <div
                className="relative h-1 flex-1 overflow-hidden rounded-full"
                style={{ background: "var(--color-hairline)" }}
              >
                <div
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{
                    width: `${Math.round(audioProgress * 100)}%`,
                    background:
                      "linear-gradient(90deg, var(--color-accent-2), var(--color-accent))",
                  }}
                />
              </div>

              <span className="shrink-0 text-[10px] tabular-nums" style={{ color: "var(--color-text-4)" }}>
                {formatAudioDuration(audioDuration)}
              </span>

              <span className="shrink-0 text-[11px]" style={{ color: "var(--color-text-3)" }}>
                {audioFile ? t("unsaved_audio") : t("saved_audio")}
              </span>

              {readOnly ? null : (
              <div className="flex shrink-0 items-center gap-0.5">
                <VoiceSampleButton
                  projectName={projectName}
                  characterName={name}
                  busy={generating || deletingAudio || Boolean(audioFile)}
                  onSaved={() => onReload?.()}
                />
                <button
                  type="button"
                  onClick={() => audioInputRef.current?.click()}
                  title={t("change")}
                  aria-label={t("upload_character_audio_ref_aria")}
                  className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
                  style={{ color: "var(--color-text-3)" }}
                >
                  <Upload className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteAudio()}
                  disabled={deletingAudio}
                  title={audioFile ? t("cancel_pending") : t("delete_audio_sample")}
                  aria-label={audioFile ? t("cancel_pending") : t("delete_audio_sample")}
                  className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ color: "var(--color-text-3)" }}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              )}

              {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 角色参考音频样本，无对白内容，无字幕源 */}
              <audio
                ref={audioElRef}
                src={displayedAudioUrl}
                preload="metadata"
                className="hidden"
                onPlay={() => setIsAudioPlaying(true)}
                onPause={() => setIsAudioPlaying(false)}
                onLoadedMetadata={(e) => setAudioDuration(e.currentTarget.duration)}
                onTimeUpdate={(e) => {
                  const el = e.currentTarget;
                  setAudioProgress(el.duration ? el.currentTime / el.duration : 0);
                }}
                onEnded={(e) => {
                  e.currentTarget.currentTime = 0;
                  setIsAudioPlaying(false);
                  setAudioProgress(0);
                }}
              />
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => audioInputRef.current?.click()}
                className="focus-ring flex flex-1 items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-hairline)] px-3 py-2.5 text-[13px] text-[var(--color-text-4)] transition-colors hover:border-[var(--color-accent-soft)] hover:text-[var(--color-text-2)]"
                style={{ background: "oklch(0.18 0.010 265 / 0.35)" }}
              >
                <Upload className="h-3.5 w-3.5" />
                {t("upload_reference_audio")}
              </button>
              {readOnly ? null : (
                <VoiceSampleButton
                  projectName={projectName}
                  characterName={name}
                  busy={generating || deletingAudio || Boolean(audioFile)}
                  onSaved={() => onReload?.()}
                />
              )}
            </div>
          )}
          {!displayedAudioUrl && (
            <p className="mt-1 text-[10px]" style={{ color: "var(--color-text-4)" }}>
              {t("reference_audio_hint")}
            </p>
          )}
          <input
            ref={audioInputRef}
            type="file"
            accept=".wav,.mp3"
            aria-label={t("upload_character_audio_ref_aria")}
            onChange={handleAudioChange}
            className="hidden"
          />
        </div>
        )}
      </div>

      {isDirty && !readOnly && (
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
          }}
        >
          {saving ? t("common:saving") : t("common:save")}
        </button>
      )}

      {readOnly ? null : (
      <div className="mt-4">
        <GenerateButton
          onClick={() => onGenerate(name)}
          loading={generating}
          label={character.character_sheet ? t("regenerate_design") : t("generate_design")}
          className="w-full justify-center"
        />
      </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        tone="danger"
        title={t(
          character.reference_audio ? "assets:delete_confirm_with_audio" : "assets:delete_confirm",
          { type: t("assets:type.character") },
        )}
        description={<span className="font-mono">「{name}」</span>}
        confirmLabel={t("assets:delete")}
        loadingLabel={t("assets:loading")}
        cancelLabel={t("assets:cancel")}
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => {
          if (!deleting) setConfirmDelete(false);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

function CapsLabel({
  children,
  htmlFor,
}: {
  children: React.ReactNode;
  htmlFor?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[10px] font-semibold uppercase tracking-[0.12em]"
      style={{ color: "var(--color-text-4)" }}
    >
      {children}
    </label>
  );
}
