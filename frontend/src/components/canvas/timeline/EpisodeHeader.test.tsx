import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EpisodeCost, EpisodeMeta } from "@/types";
import { EpisodeHeader } from "./EpisodeHeader";

const EP: EpisodeMeta = { episode: 1, title: "Ep1", script_file: "episode_1.json" };

function episodeCost(actual: EpisodeCost["totals"]["actual"]): EpisodeCost {
  return {
    episode: 1,
    title: "Ep1",
    segments: [],
    totals: { estimate: { video: { USD: 10 } }, actual },
  };
}

describe("EpisodeHeader", () => {
  it("counts history spend as spent but not against the remaining estimate", () => {
    render(
      <EpisodeHeader
        ep={EP}
        segmentCount={1}
        totalDuration={5}
        episodeCost={episodeCost({ unassigned: { USD: 10 } })}
      />,
    );

    // 已花含历史支出；剩余仍是整笔预估——那 $10 花在已被替换掉的旧剧本上，
    // 当前剧本的工作一件也没做。
    expect(screen.getAllByText("$10.00")).toHaveLength(3);
  });

  it("deducts current-script spend from the remaining estimate", () => {
    render(
      <EpisodeHeader
        ep={EP}
        segmentCount={1}
        totalDuration={5}
        episodeCost={episodeCost({ video: { USD: 4 } })}
      />,
    );

    expect(screen.getByText("$4.00")).toBeInTheDocument();
    expect(screen.getByText("$6.00")).toBeInTheDocument();
  });

  it("disables compose preview until every shot has a clip", () => {
    render(
      <EpisodeHeader
        ep={EP}
        segmentCount={2}
        totalDuration={10}
        canComposePreview={false}
        onComposePreview={vi.fn()}
        onUpscalePreview={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /合成本集预览/ })).toBeDisabled();
    // 无 episodePreviewUrl / onWatchPreview 时超分不可用（与参考生视频集头同口径）
    expect(screen.getByRole("button", { name: /^超分$/ })).toBeDisabled();
  });

  it("composes the episode preview when all shots are ready", async () => {
    const onComposePreview = vi.fn().mockResolvedValue({
      url: "/api/v1/files/proj/output/episode_1_preview.mp4",
      revision: 1,
    });

    render(
      <EpisodeHeader
        ep={EP}
        segmentCount={1}
        totalDuration={5}
        canComposePreview
        onComposePreview={onComposePreview}
        onWatchPreview={vi.fn()}
        onUpscalePreview={vi.fn()}
        onWatchUpscaled={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /合成本集预览/ }));
    expect(onComposePreview).toHaveBeenCalledTimes(1);
  });
});
