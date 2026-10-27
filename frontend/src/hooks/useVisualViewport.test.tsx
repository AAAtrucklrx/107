import { renderHook, act } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useVisualViewport } from "./useVisualViewport";

afterEach(() => vi.unstubAllGlobals());

test("resizes for an overlay keyboard, ignores zoom and cleans up viewport listeners", () => {
  const viewport = Object.assign(new EventTarget(), { height: 740, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  const input = document.createElement("textarea");
  document.body.append(input);
  const { unmount } = renderHook(useVisualViewport);
  input.focus();
  act(() => { viewport.height = 390; viewport.offsetTop = 12; viewport.dispatchEvent(new Event("resize")); });
  expect(document.documentElement.style.getPropertyValue("--app-viewport-height")).toBe("390px");
  expect(document.documentElement.dataset.keyboardOpen).toBe("true");
  act(() => { viewport.scale = 2; viewport.height = 200; viewport.dispatchEvent(new Event("resize")); });
  expect(document.documentElement.style.getPropertyValue("--app-viewport-height")).toBe("390px");
  unmount();
  viewport.scale = 1;
  viewport.dispatchEvent(new Event("resize"));
  expect(document.documentElement.style.getPropertyValue("--app-viewport-height")).toBe("");
  expect(document.documentElement.dataset.keyboardOpen).toBeUndefined();
  input.remove();
});

test("recognizes keyboards that resize both layout and visual viewports", () => {
  const viewport = Object.assign(new EventTarget(), { height: 800, offsetTop: 0, scale: 1 });
  vi.stubGlobal("visualViewport", viewport);
  vi.stubGlobal("innerHeight", 800);
  const input = document.createElement("textarea"); document.body.append(input);
  const { unmount } = renderHook(useVisualViewport);
  input.focus();
  act(() => { viewport.height = 400; vi.stubGlobal("innerHeight", 400); viewport.dispatchEvent(new Event("resize")); });
  expect(document.documentElement.dataset.keyboardOpen).toBe("true");
  unmount(); input.remove();
});
