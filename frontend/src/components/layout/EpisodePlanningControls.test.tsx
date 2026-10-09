import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_PLANNING_MAX_EPISODES,
  DEFAULT_PLANNING_WINDOW_CHARS,
  EpisodePlanningControls,
} from "@/components/layout/EpisodePlanningControls";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

describe("EpisodePlanningControls", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  it("shows defaults when project has no planning overrides", () => {
    useProjectsStore.getState().setCurrentProject(
      "demo-novel",
      {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [],
        characters: {},
      } as ProjectData,
      {},
    );

    render(<EpisodePlanningControls />);

    expect(screen.getByLabelText("切分")).toHaveValue("llm");
    expect(screen.getByLabelText("窗口")).toHaveValue(String(DEFAULT_PLANNING_WINDOW_CHARS));
    expect(screen.getByLabelText("每批")).toHaveValue(String(DEFAULT_PLANNING_MAX_EPISODES));
    expect(screen.getByLabelText("自动确认")).not.toBeChecked();
  });

  it("persists auto_confirm_step1 via updateProject", async () => {
    const user = userEvent.setup();
    useProjectsStore.getState().setCurrentProject(
      "demo-novel",
      {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [],
        characters: {},
      } as ProjectData,
      {},
    );
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [],
        characters: {},
        auto_confirm_step1: true,
      } as ProjectData,
    });

    render(<EpisodePlanningControls />);
    await user.click(screen.getByLabelText("自动确认"));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo-novel", { auto_confirm_step1: true });
    });
    expect(useProjectsStore.getState().currentProjectData?.auto_confirm_step1).toBe(true);
  });

  it("persists batch size via updateProject", async () => {
    const user = userEvent.setup();
    useProjectsStore.getState().setCurrentProject(
      "demo-novel",
      {
        title: "Novel",
        content_mode: "narration",
        style: "",
        episodes: [],
        characters: {},
        planning_window_chars: 20000,
        planning_max_episodes: 20,
      } as ProjectData,
      {},
    );
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: {
        title: "Novel",
        content_mode: "narration",
        style: "",
        episodes: [],
        characters: {},
        planning_window_chars: 20000,
        planning_max_episodes: 10,
      } as ProjectData,
    });

    render(<EpisodePlanningControls />);
    await user.selectOptions(screen.getByLabelText("每批"), "10");

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo-novel", { planning_max_episodes: 10 });
    });
    expect(useProjectsStore.getState().currentProjectData?.planning_max_episodes).toBe(10);
  });

  it("persists split mode and shows run button for markers", async () => {
    const user = userEvent.setup();
    useProjectsStore.getState().setCurrentProject(
      "demo-novel",
      {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [],
        characters: {},
      } as ProjectData,
      {},
    );
    const updateSpy = vi.spyOn(API, "updateProject").mockResolvedValue({
      success: true,
      project: {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [],
        characters: {},
        planning_split_mode: "markers",
      } as ProjectData,
    });

    render(<EpisodePlanningControls />);
    await user.selectOptions(screen.getByLabelText("切分"), "markers");

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("demo-novel", { planning_split_mode: "markers" });
    });
    expect(screen.getByRole("button", { name: "执行切分" })).toBeInTheDocument();
    expect(screen.queryByLabelText("窗口")).not.toBeInTheDocument();
  });

  it("runs marker split with overwrite confirm when episodes exist", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    useProjectsStore.getState().setCurrentProject(
      "demo-novel",
      {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [{ episode: 1, title: "旧" }],
        characters: {},
        planning_split_mode: "markers",
      } as ProjectData,
      {},
    );
    const planSpy = vi.spyOn(API, "planEpisodesByMarkers").mockResolvedValue({
      success: true,
      project: {
        title: "Novel",
        content_mode: "drama",
        style: "",
        episodes: [{ episode: 1, title: "新" }],
        characters: {},
        planning_split_mode: "markers",
      } as ProjectData,
      episodes: [
        { episode: 1, title: "新", hook: "h", reading_units: 10, ledger_status: "planned" },
      ],
      total_planned: 1,
      source_exhausted: true,
      stale_episodes: [],
    });

    render(<EpisodePlanningControls />);
    await user.click(screen.getByRole("button", { name: "执行切分" }));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalled();
      expect(planSpy).toHaveBeenCalledWith("demo-novel", { overwrite: true });
    });
    expect(useProjectsStore.getState().currentProjectData?.episodes?.[0]?.title).toBe("新");
  });

  it("hides for ad projects", () => {
    useProjectsStore.getState().setCurrentProject(
      "ad-demo",
      {
        title: "Ad",
        content_mode: "ad",
        style: "",
        episodes: [],
        characters: {},
      } as ProjectData,
      {},
    );

    const { container } = render(<EpisodePlanningControls />);
    expect(container).toBeEmptyDOMElement();
  });
});
