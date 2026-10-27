import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test } from "vitest";
import { AppearanceProvider } from "./AppearanceProvider";
import { FamilyPicker, FamilyShortcut } from "./FamilyPicker";
import { StylePicker } from "./StylePicker";

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); delete document.documentElement.dataset.style; });

test("keyboard selection in the shortcut closes its menu, restores focus, and updates profile selection", async () => {
  const user = userEvent.setup();
  render(<AppearanceProvider><FamilyShortcut /><FamilyPicker /></AppearanceProvider>);
  const trigger = screen.getByRole("button", { name: "切换奶家人，当前奶蛙" });
  await act(async () => { trigger.focus(); });
  await user.keyboard("{Enter}");
  const egg = screen.getByRole("menuitemradio", { name: "奶蛋" });
  await act(async () => { egg.focus(); });
  await user.keyboard("{Enter}");
  await waitFor(() => {
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换奶家人，当前奶蛋" })).toHaveFocus();
  });
  expect(screen.getByRole("button", { name: "奶蛋" })).toHaveAttribute("aria-pressed", "true");
  expect(localStorage.getItem("xiaowa-family-character")).toBe("egg");
});

test("profile switches only character and keeps the chosen campus visual style", async () => {
  const user = userEvent.setup();
  render(<AppearanceProvider><FamilyPicker /><StylePicker /></AppearanceProvider>);
  await user.click(screen.getByRole("button", { name: "科大蓝" }));
  await user.click(screen.getByRole("button", { name: "奶鼠" }));
  expect(screen.getByRole("button", { name: "奶鼠" })).toHaveAttribute("aria-pressed", "true");
  expect(document.documentElement.dataset.style).toBe("ustc");
  expect(localStorage.getItem("xiaowa-family-character")).toBe("mouse");
});
