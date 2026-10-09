import type { Character, Prop, Scene } from "@/types";

export type MissingSheetItem = { name: string; prompt: string };

const BASE_LOOK_ID = "base";

/**
 * 尚无资产图、可一键入队的角色项：基准用角色名；非 base 造型用 `{名}__{look}`。
 */
export function collectMissingCharacterSheetItems(
  characters: Record<string, Character>,
): MissingSheetItem[] {
  const items: MissingSheetItem[] = [];
  for (const [name, character] of Object.entries(characters)) {
    if (!character.character_sheet) {
      items.push({ name, prompt: character.description ?? "" });
    }
    for (const [lookId, look] of Object.entries(character.looks ?? {})) {
      if (lookId === BASE_LOOK_ID) continue;
      if (look.character_sheet) continue;
      items.push({
        name: `${name}__${lookId}`,
        prompt: look.description ?? "",
      });
    }
  }
  return items;
}

export function collectMissingSceneSheetItems(
  scenes: Record<string, Scene>,
): MissingSheetItem[] {
  return Object.entries(scenes)
    .filter(([, scene]) => !scene.scene_sheet)
    .map(([name, scene]) => ({ name, prompt: scene.description ?? "" }));
}

export function collectMissingPropSheetItems(
  props: Record<string, Prop>,
): MissingSheetItem[] {
  return Object.entries(props)
    .filter(([, prop]) => !prop.prop_sheet)
    .map(([name, prop]) => ({ name, prompt: prop.description ?? "" }));
}
