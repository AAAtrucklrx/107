import idle from "./assets/idle.webp";
import laugh from "./assets/laugh.webp";
import laughOriginal from "./assets/laugh-original.gif";
import think from "./assets/think.webp";
import sleep from "./assets/sleep.webp";
import celebrate from "./assets/celebrate.webp";
import surprised from "./assets/surprised.webp";
import eat from "./assets/eat.webp";
import type { MascotState } from "./state";

export const mascotAssets: Record<MascotState, string> = { idle, laugh, think, sleep, celebrate, surprised, eat };
export const mascotLaughAnimation = laughOriginal;
export function preloadMascotReactions(reducedMotion = false) {
  const sources = Object.entries(mascotAssets).filter(([state]) => state !== "idle").map(([, src]) => src);
  if (!reducedMotion) sources.push(mascotLaughAnimation);
  const preload = () => sources.forEach((src) => {
    const image = new Image(); image.decoding = "async"; image.src = src;
  });
  if (typeof window.requestIdleCallback === "function") {
    const id = window.requestIdleCallback(preload, { timeout: 3000 });
    return () => window.cancelIdleCallback(id);
  }
  const id = window.setTimeout(preload, 1200);
  return () => window.clearTimeout(id);
}
