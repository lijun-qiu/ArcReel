import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GalleryToolbar } from "./GalleryToolbar";

describe("GalleryToolbar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows generate-all button and invokes callback", async () => {
    const user = userEvent.setup();
    const onGenerateAll = vi.fn();
    render(
      <GalleryToolbar
        title="角色"
        count={2}
        onGenerateAll={onGenerateAll}
      />,
    );
    const btn = screen.getByRole("button", { name: /一键生成资产图/ });
    await user.click(btn);
    expect(onGenerateAll).toHaveBeenCalledTimes(1);
  });

  it("disables generate-all when generateAllDisabled", () => {
    render(
      <GalleryToolbar
        title="角色"
        count={2}
        onGenerateAll={vi.fn()}
        generateAllDisabled
      />,
    );
    expect(screen.getByRole("button", { name: /一键生成资产图/ })).toBeDisabled();
  });

  it("hides generate-all when callback omitted", () => {
    render(<GalleryToolbar title="角色" count={0} />);
    expect(screen.queryByRole("button", { name: /一键生成资产图/ })).toBeNull();
  });
});
