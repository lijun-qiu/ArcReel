import { describe, expect, it } from "vitest";
import {
  collectMissingCharacterSheetItems,
  collectMissingPropSheetItems,
  collectMissingSceneSheetItems,
} from "./missing-asset-sheets";

describe("collectMissingCharacterSheetItems", () => {
  it("includes base characters and non-base looks without sheets", () => {
    const items = collectMissingCharacterSheetItems({
      林北: {
        description: "少年",
        character_sheet: "characters/林北.png",
        looks: {
          base: { description: "少年", character_sheet: "characters/林北.png" },
          战损: { description: "夹克撕破" },
        },
      },
      阿宁: {
        description: "少女",
        looks: {
          base: { description: "少女" },
        },
      },
    });

    expect(items).toEqual([
      { name: "林北__战损", prompt: "夹克撕破" },
      { name: "阿宁", prompt: "少女" },
    ]);
  });

  it("skips looks that already have sheets", () => {
    expect(
      collectMissingCharacterSheetItems({
        林北: {
          description: "少年",
          character_sheet: "characters/林北.png",
          looks: {
            base: { description: "少年", character_sheet: "characters/林北.png" },
            战损: {
              description: "夹克撕破",
              character_sheet: "characters/林北__战损.png",
            },
          },
        },
      }),
    ).toEqual([]);
  });
});

describe("collectMissingSceneSheetItems / collectMissingPropSheetItems", () => {
  it("keeps only entries without sheets", () => {
    expect(
      collectMissingSceneSheetItems({
        教室: { description: "白天教室", scene_sheet: "scenes/教室.png" },
        天台: { description: "夜天台" },
      }),
    ).toEqual([{ name: "天台", prompt: "夜天台" }]);

    expect(
      collectMissingPropSheetItems({
        钥匙: { description: "旧铜钥" },
        手表: { description: "怀表", prop_sheet: "props/手表.png" },
      }),
    ).toEqual([{ name: "钥匙", prompt: "旧铜钥" }]);
  });
});
