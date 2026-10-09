import { describe, expect, it } from "vitest";
import {
  previewCharacterLookPrompt,
  previewCharacterPrompt,
} from "./character-prompt-preview";

describe("character-prompt-preview", () => {
  it("keeps project style verbatim and strips non-appearance clauses", () => {
    const style = "银翼杀手2049风格，极简野蛮主义赛博朋克，只用一种颜色来统治画面，留白";
    const prompt = previewCharacterPrompt(
      "林北",
      "短黑发冷峻，手持手枪走进废墟",
      style,
      "",
    );
    expect(prompt).toContain(`风格：${style}`);
    expect(prompt).toContain("短黑发冷峻");
    expect(prompt).not.toContain("手持手枪");
    expect(prompt).not.toContain("走进废墟");
    expect(prompt).toContain("设计参考图");
    expect(prompt).toContain("四格布局");
    expect(prompt).toContain("五官对称");
    expect(prompt).not.toContain("衣着崭新平整");
    expect(prompt).not.toContain("基准造型");
    expect(prompt).not.toContain("禁止漂成真人");
    expect(prompt.indexOf("风格：")).toBeLessThan(prompt.indexOf("设计参考图"));
  });

  it("strips plot bio so style can lead", () => {
    const style = "参考动画赛博朋克电影，电影质感，极致高清画质";
    const prompt = previewCharacterPrompt(
      "林北",
      "本作男主，重生者。前世被杀害。二十多岁青年男性，湿发贴额，眼神冷静。",
      style,
      "",
    );
    expect(prompt).toContain(`风格：${style}`);
    expect(prompt).toContain("二十多岁青年男性");
    expect(prompt).toContain("湿发贴额");
    expect(prompt).not.toContain("本作男主");
    expect(prompt).not.toContain("重生者");
    expect(prompt).not.toContain("杀害");
  });

  it("includes look diff and base description for wardrobe sheets", () => {
    const prompt = previewCharacterLookPrompt("林北", "战损", "夹克撕破", {
      baseDescription: "短黑发冷峻",
      style: "都市国漫偶像",
    });
    expect(prompt).toContain("衣橱造型「战损」");
    expect(prompt).toContain("风格：都市国漫偶像");
    expect(prompt).toContain("基准外形：短黑发冷峻");
    expect(prompt).toContain("本造型差异：夹克撕破");
    expect(prompt).not.toContain("衣着崭新平整");
    expect(prompt.indexOf("风格：")).toBeLessThan(prompt.indexOf("衣橱造型"));
  });
});
