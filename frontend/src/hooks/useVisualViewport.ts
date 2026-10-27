import { useEffect } from "react";

/** Keep the app shell inside the visible viewport when an overlay keyboard opens. */
export function useVisualViewport() {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const root = document.documentElement;
    let expandedHeight = Math.max(window.innerHeight, viewport.height);
    let disposed = false;
    const update = () => {
      // Pinch zoom should remain a browser accessibility feature, not resize the UI.
      if (viewport.scale !== 1) return;
      root.style.setProperty("--app-viewport-height", `${viewport.height}px`);
      root.style.setProperty("--app-viewport-top", `${viewport.offsetTop}px`);
      const focused = document.activeElement;
      const editing = focused instanceof HTMLElement && (focused.matches("input, textarea") || focused.isContentEditable);
      if (!editing) expandedHeight = Math.max(window.innerHeight, viewport.height);
      root.dataset.keyboardOpen = String(editing && Math.max(window.innerHeight, expandedHeight) - viewport.height > 100);
    };
    const afterFocus = () => queueMicrotask(() => { if (!disposed) update(); });
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", afterFocus);
    return () => {
      disposed = true;
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", afterFocus);
      root.style.removeProperty("--app-viewport-height");
      root.style.removeProperty("--app-viewport-top");
      delete root.dataset.keyboardOpen;
    };
  }, []);
}
