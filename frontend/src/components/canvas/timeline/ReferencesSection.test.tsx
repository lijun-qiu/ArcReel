import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReferencesSection } from "./ReferencesSection";
import { useProjectsStore } from "@/stores/projects-store";

vi.mock("@/api", () => ({
  API: {
    getFileUrl: (_project: string, path: string) => `/files/${path}`,
  },
}));

describe("ReferencesSection previous storyboard", () => {
  beforeEach(() => {
    useProjectsStore.setState({
      currentProjectData: {
        characters: {},
        scenes: {},
        props: {},
      } as never,
      assetFingerprints: {},
    } as never);
  });

  it("does not show previous group when previousStoryboard is null", () => {
    render(
      <ReferencesSection
        projectName="demo"
        contentMode="narration"
        characterNames={["Alice"]}
        sceneNames={[]}
        propNames={[]}
        onSave={vi.fn()}
        previousStoryboard={null}
      />,
    );
    expect(screen.queryByText("上一镜")).not.toBeInTheDocument();
  });

  it("shows previous storyboard and toggles exclude/restore", async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <ReferencesSection
        projectName="demo"
        contentMode="narration"
        characterNames={[]}
        sceneNames={[]}
        propNames={[]}
        onSave={vi.fn()}
        previousStoryboard={{ id: "E1S01", path: "storyboards/scene_E1S01.png" }}
        usePreviousStoryboard
        onTogglePreviousStoryboard={onToggle}
      />,
    );

    expect(screen.getByText("上一镜")).toBeInTheDocument();
    expect(screen.getByText("E1S01")).toBeInTheDocument();
    expect(screen.getByAltText("上一镜 E1S01 分镜图")).toHaveAttribute(
      "src",
      "/files/storyboards/scene_E1S01.png",
    );

    fireEvent.click(screen.getByRole("button", { name: "排除" }));
    await waitFor(() => expect(onToggle).toHaveBeenCalledWith(false));

    rerender(
      <ReferencesSection
        projectName="demo"
        contentMode="narration"
        characterNames={[]}
        sceneNames={[]}
        propNames={[]}
        onSave={vi.fn()}
        previousStoryboard={{ id: "E1S01", path: "storyboards/scene_E1S01.png" }}
        usePreviousStoryboard={false}
        onTogglePreviousStoryboard={onToggle}
      />,
    );
    expect(screen.getByText("已排除")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    await waitFor(() => expect(onToggle).toHaveBeenCalledWith(true));
  });

  it("hides exclude control when toggle callback is omitted", () => {
    render(
      <ReferencesSection
        projectName="demo"
        contentMode="narration"
        characterNames={[]}
        sceneNames={[]}
        propNames={[]}
        onSave={vi.fn()}
        previousStoryboard={{ id: "E1S01", path: "storyboards/scene_E1S01.png" }}
        usePreviousStoryboard
      />,
    );
    expect(screen.getByText("上一镜")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "排除" })).not.toBeInTheDocument();
  });
});
