import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useEffect, useState } from "react";
import { AppearanceProvider, useAppearance } from "./AppearanceProvider";

function Probe() {
  const { style, setStyle } = useAppearance();
  return <><output>{style}</output><button onClick={() => setStyle(style === "frog" ? "ustc" : "frog")}>切换风格</button></>;
}
beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); delete document.documentElement.dataset.style; });
test("defaults to frog and restores a saved campus style without changing dark mode", () => {
  document.documentElement.dataset.theme = "dark";
  const first = render(<AppearanceProvider><Probe /></AppearanceProvider>);
  expect(screen.getByRole("status")).toHaveTextContent("frog");
  fireEvent.click(screen.getByRole("button", { name: "切换风格" }));
  expect(document.documentElement.dataset.style).toBe("ustc");
  expect(document.documentElement.dataset.theme).toBe("dark");
  first.unmount();
  render(<AppearanceProvider><Probe /></AppearanceProvider>);
  expect(screen.getByRole("status")).toHaveTextContent("ustc");
});
test("ignores invalid preferences and still switches when storage is unavailable", () => {
  localStorage.setItem("xiaowa-visual-style", "invalid");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavailable"); });
  render(<AppearanceProvider><Probe /></AppearanceProvider>);
  expect(screen.getByRole("status")).toHaveTextContent("frog");
  fireEvent.click(screen.getByRole("button", { name: "切换风格" }));
  expect(screen.getByRole("status")).toHaveTextContent("ustc");
});

function FamilyProbe({ onMount = () => {} }: { onMount?: () => void }) {
  const { character, setCharacter } = useAppearance();
  const [draft, setDraft] = useState("");
  useEffect(() => { onMount(); }, [onMount]);
  return <><output aria-label="当前角色">{character}</output><input aria-label="草稿" value={draft} onChange={(event) => setDraft(event.target.value)} /><button onClick={() => setCharacter("egg")}>选奶蛋</button><button onClick={() => setCharacter("mouse")}>选奶鼠</button></>;
}

test("persists the chosen family member without remounting chat children or clearing their draft", () => {
  const mounted = vi.fn();
  const first = render(<AppearanceProvider><FamilyProbe onMount={mounted} /></AppearanceProvider>);
  expect(screen.getByRole("status", { name: "当前角色" })).toHaveTextContent("frog");
  fireEvent.change(screen.getByRole("textbox", { name: "草稿" }), { target: { value: "下周的课表" } });
  fireEvent.click(screen.getByRole("button", { name: "选奶蛋" }));
  expect(screen.getByRole("status", { name: "当前角色" })).toHaveTextContent("egg");
  expect(screen.getByRole("textbox", { name: "草稿" })).toHaveValue("下周的课表");
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem("xiaowa-family-character")).toBe("egg");
  first.unmount();
  render(<AppearanceProvider><FamilyProbe /></AppearanceProvider>);
  expect(screen.getByRole("status", { name: "当前角色" })).toHaveTextContent("egg");
});

test("falls back to frog for invalid saved characters and changes locally if storage is blocked", () => {
  localStorage.setItem("xiaowa-family-character", "unknown");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavailable"); });
  render(<AppearanceProvider><FamilyProbe /></AppearanceProvider>);
  expect(screen.getByRole("status", { name: "当前角色" })).toHaveTextContent("frog");
  fireEvent.click(screen.getByRole("button", { name: "选奶鼠" }));
  expect(screen.getByRole("status", { name: "当前角色" })).toHaveTextContent("mouse");
});
