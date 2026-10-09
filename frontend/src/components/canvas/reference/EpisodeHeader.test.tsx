import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCostStore } from "@/stores/cost-store";
import type { CostByType } from "@/types";
import { EpisodeHeader } from "./EpisodeHeader";

const UNITS = [{ duration_seconds: 5, generated_assets: { video_clip: "a.mp4" } }];
const PARTIAL_UNITS = [
  { duration_seconds: 5, generated_assets: { video_clip: "a.mp4" } },
  { duration_seconds: 5, generated_assets: {} },
];

function seedEpisodeCost(actual: CostByType) {
  useCostStore.setState({
    _episodeIndex: new Map([
      [
        1,
        {
          episode: 1,
          title: "EP1",
          segments: [],
          totals: { estimate: { video: { USD: 8 } }, actual },
        },
      ],
    ]),
  });
}

afterEach(() => {
  useCostStore.setState(useCostStore.getInitialState(), true);
});

describe("reference EpisodeHeader", () => {
  it("counts history and audio spend in the episode actual", () => {
    seedEpisodeCost({ video: { USD: 2 }, audio: { USD: 0.5 }, unassigned: { USD: 1.25 } });

    render(<EpisodeHeader episode={1} title="EP1" units={UNITS} />);

    expect(screen.getByText("$3.75")).toBeInTheDocument();
  });

  it("shows history spend even when the current units have none", () => {
    seedEpisodeCost({ unassigned: { USD: 1.25 } });

    render(<EpisodeHeader episode={1} title="EP1" units={UNITS} />);

    expect(screen.getByText("$1.25")).toBeInTheDocument();
  });

  it("disables compose preview until every unit has a clip", () => {
    render(
      <EpisodeHeader
        episode={1}
        title="EP1"
        units={PARTIAL_UNITS}
        onComposePreview={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /合成本集预览/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /播放预览/ })).toBeDisabled();
  });

  it("enables watch when an episode preview url is provided", () => {
    render(
      <EpisodeHeader
        episode={1}
        title="EP1"
        units={UNITS}
        onComposePreview={vi.fn()}
        episodePreviewUrl="/api/v1/files/proj/output/episode_1_preview.mp4?v=1"
        episodePreviewSubtitlesWebvtt={"WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\n机械字幕\n"}
        episodePreviewSubtitles={[
          { start_microseconds: 0, duration_microseconds: 5_000_000, text: "机械字幕" },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /播放预览/ }));
    const player = screen.getByRole("dialog", { name: /本集预览/ });
    expect(player.querySelector("video")?.getAttribute("src")).toBe(
      "/api/v1/files/proj/output/episode_1_preview.mp4?v=1",
    );
    expect(player.querySelector("track")).toHaveAttribute("kind", "captions");
  });

  it("composes the episode preview and opens the player", async () => {
    const onComposePreview = vi.fn().mockResolvedValue({
      url: "/api/v1/files/proj/output/episode_1_preview.mp4",
      subtitles_webvtt: "WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\n机械字幕\n",
      subtitles: [{ start_microseconds: 0, duration_microseconds: 5_000_000, text: "机械字幕" }],
    });

    render(
      <EpisodeHeader episode={1} title="EP1" units={UNITS} onComposePreview={onComposePreview} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /合成本集预览/ }));

    await waitFor(() => {
      expect(onComposePreview).toHaveBeenCalledTimes(1);
    });
    const player = await screen.findByRole("dialog", { name: /本集预览/ });
    expect(player.querySelector("video")?.getAttribute("src")).toMatch(
      /^\/api\/v1\/files\/proj\/output\/episode_1_preview\.mp4\?v=/,
    );
    expect(player.querySelector("track")).toHaveAttribute("kind", "captions");
  });

  it("disables upscale until an episode preview exists", () => {
    render(
      <EpisodeHeader
        episode={1}
        title="EP1"
        units={UNITS}
        onComposePreview={vi.fn()}
        onUpscalePreview={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /^超分$/ })).toBeDisabled();
  });

  it("upscales the episode preview and reopens the player", async () => {
    const onUpscalePreview = vi.fn().mockResolvedValue({
      url: "/api/v1/files/proj/output/episode_1_preview_upscaled.mp4",
      subtitles_webvtt: null,
      subtitles: [],
    });

    render(
      <EpisodeHeader
        episode={1}
        title="EP1"
        units={UNITS}
        onComposePreview={vi.fn()}
        onUpscalePreview={onUpscalePreview}
        episodePreviewUrl="/api/v1/files/proj/output/episode_1_preview.mp4?v=1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^超分$/ }));

    await waitFor(() => {
      expect(onUpscalePreview).toHaveBeenCalledTimes(1);
    });
    const player = await screen.findByRole("dialog", { name: /本集预览/ });
    expect(player.querySelector("video")?.getAttribute("src")).toMatch(
      /episode_1_preview_upscaled\.mp4\?v=/,
    );
  });

  it("plays the upscaled file from a separate button", async () => {
    render(
      <EpisodeHeader
        episode={1}
        title="EP1"
        units={UNITS}
        onComposePreview={vi.fn()}
        onWatchUpscaled={async () => ({
          url: "/api/v1/files/proj/output/episode_1_preview_upscaled.mp4?v=9",
          subtitles_webvtt: null,
          subtitles: [],
        })}
        episodeUpscaledUrl="/api/v1/files/proj/output/episode_1_preview_upscaled.mp4?v=9"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /播放超分/ }));
    const player = await screen.findByRole("dialog", { name: /本集预览/ });
    expect(player.querySelector("video")?.getAttribute("src")).toBe(
      "/api/v1/files/proj/output/episode_1_preview_upscaled.mp4?v=9",
    );
  });
});
