import { describe, expect, it } from "vitest";
import {
  itemUsesPreviousStoryboard,
  resolvePreviousStoryboardRef,
  selectTimelineBatchTargets,
} from "./script-shape";
import type { NarrationEpisodeScript, NarrationSegment } from "@/types";

function seg(
  id: string,
  opts: {
    segment_break?: boolean;
    storyboard?: string;
    video?: string;
    use_previous?: boolean;
  } = {},
): NarrationSegment {
  return {
    segment_id: id,
    episode: 1,
    duration_seconds: 4,
    segment_break: opts.segment_break ?? false,
    novel_text: "",
    characters_in_segment: [],
    image_prompt: "p",
    video_prompt: "v",
    transition_to_next: "cut",
    use_previous_storyboard: opts.use_previous,
    generated_assets:
      opts.storyboard || opts.video
        ? {
            storyboard_image: opts.storyboard ?? null,
            video_clip: opts.video ?? null,
            status: opts.video ? "ready" : "storyboard_ready",
          }
        : undefined,
  } as NarrationSegment;
}

function narrationScript(segments: NarrationSegment[]): NarrationEpisodeScript {
  return {
    episode: 1,
    title: "EP1",
    content_mode: "narration",
    novel: { title: "n", chapter: "1" },
    segments,
  };
}

describe("resolvePreviousStoryboardRef", () => {
  it("returns null for the first item", () => {
    const items = [seg("E1S01", { storyboard: "storyboards/a.png" }), seg("E1S02")];
    expect(resolvePreviousStoryboardRef(items, "narration", "E1S01")).toBeNull();
  });

  it("returns previous storyboard when available", () => {
    const items = [
      seg("E1S01", { storyboard: "storyboards/a.png" }),
      seg("E1S02"),
    ];
    expect(resolvePreviousStoryboardRef(items, "narration", "E1S02")).toEqual({
      id: "E1S01",
      path: "storyboards/a.png",
    });
  });

  it("returns null when current item is a segment break", () => {
    const items = [
      seg("E1S01", { storyboard: "storyboards/a.png" }),
      seg("E1S02", { segment_break: true }),
    ];
    expect(resolvePreviousStoryboardRef(items, "narration", "E1S02")).toBeNull();
  });

  it("returns null when previous has no storyboard image", () => {
    const items = [seg("E1S01"), seg("E1S02")];
    expect(resolvePreviousStoryboardRef(items, "narration", "E1S02")).toBeNull();
  });
});

describe("itemUsesPreviousStoryboard", () => {
  it("defaults to false when missing", () => {
    expect(itemUsesPreviousStoryboard({})).toBe(false);
    expect(itemUsesPreviousStoryboard({ use_previous_storyboard: false })).toBe(false);
  });

  it("returns true only when explicitly true", () => {
    expect(itemUsesPreviousStoryboard({ use_previous_storyboard: true })).toBe(true);
  });
});

describe("selectTimelineBatchTargets", () => {
  it("selects all or missing storyboards", () => {
    const script = narrationScript([
      seg("E1S01", { storyboard: "storyboards/a.png" }),
      seg("E1S02"),
    ]);
    expect(selectTimelineBatchTargets(script, "narration", "storyboard", "all")).toEqual([
      "E1S01",
      "E1S02",
    ]);
    expect(selectTimelineBatchTargets(script, "narration", "storyboard", "missing")).toEqual([
      "E1S02",
    ]);
  });

  it("requires a storyboard before selecting video targets", () => {
    const script = narrationScript([
      seg("E1S01"),
      seg("E1S02", { storyboard: "storyboards/b.png" }),
      seg("E1S03", { storyboard: "storyboards/c.png", video: "videos/c.mp4" }),
    ]);
    expect(selectTimelineBatchTargets(script, "narration", "video", "all")).toEqual([
      "E1S02",
      "E1S03",
    ]);
    expect(selectTimelineBatchTargets(script, "narration", "video", "missing")).toEqual(["E1S02"]);
  });
});
