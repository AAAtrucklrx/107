import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Mascot } from "./Mascot";
import { MASCOT_EVENT, type MascotEvent } from "./events";
import { greeting, localDay } from "./lines";
import { initialMascot, mascotReducer, reactionDuration } from "./state";
import { preloadMascotReactions } from "./assets";
import "./mascot.css";
export { emitMascot } from "./events";

function useMedia(query: string) {
  const [matches, setMatches] = useState(() => typeof window.matchMedia === "function" && window.matchMedia(query).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const change = () => setMatches(media.matches);
    change(); media.addEventListener?.("change", change);
    return () => media.removeEventListener?.("change", change);
  }, [query]);
  return matches;
}
const read = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };
const write = (key: string, value: string) => { try { localStorage.setItem(key, value); } catch { /* Companionship still works without browser storage. */ } };

export function MascotLayer() {
  const [model, dispatch] = useReducer(mascotReducer, initialMascot);
  const modelRef = useRef(model); modelRef.current = model;
  const sleepTimer = useRef<number | undefined>(undefined);
  const desktop = useMedia("(min-width: 1025px)");
  const reducedMotion = useMedia("(prefers-reduced-motion: reduce)");
  const activity = useCallback(() => {
    window.clearTimeout(sleepTimer.current);
    if (modelRef.current.state === "sleep") dispatch({ type: "wake" });
    sleepTimer.current = window.setTimeout(() => dispatch({ type: "sleep" }), 60_000);
  }, []);
  useEffect(() => preloadMascotReactions(reducedMotion), [reducedMotion]);
  useEffect(() => {
    const onEvent = (event: Event) => {
      const detail = (event as CustomEvent<MascotEvent>).detail;
      if (!detail || !["think", "success", "error", "idle", "eat"].includes(detail.type)) return;
      dispatch({ type: "event", event: { ...detail, message: typeof detail.message === "string" ? detail.message.slice(0, 120) : undefined } });
    };
    window.addEventListener(MASCOT_EVENT, onEvent);
    const events = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart"] as const;
    events.forEach((event) => window.addEventListener(event, activity, { passive: true }));
    activity();
    return () => {
      window.removeEventListener(MASCOT_EVENT, onEvent);
      events.forEach((event) => window.removeEventListener(event, activity));
      window.clearTimeout(sleepTimer.current);
    };
  }, [activity]);
  useEffect(() => {
    if (model.state === "sleep") return;
    activity();
    const duration = reactionDuration[model.state];
    if (!duration) return;
    const timer = window.setTimeout(() => dispatch({ type: "settle" }), duration);
    return () => window.clearTimeout(timer);
  }, [model.revision, model.state, activity]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const day = localDay();
      if (read("xiaowo.mascot.greeting-day") === day || modelRef.current.state !== "idle") return;
      write("xiaowo.mascot.greeting-day", day);
      dispatch({ type: "laugh", message: greeting() });
    }, 500);
    return () => window.clearTimeout(timer);
  }, []);
  const pet = () => {
    const previous = Number(read("xiaowo.mascot.affection") || 0);
    const affection = Number.isFinite(previous) ? Math.max(0, Math.min(100, previous + 1)) : 1;
    write("xiaowo.mascot.affection", String(affection));
    dispatch({ type: "pet" });
  };
  return (
    <aside className="mascot-layer" aria-label="奶蛙陪伴" data-placement={desktop ? "corner" : "inline"} data-reduced-motion={String(reducedMotion)}>
      <div className="mascot-copy">
        <p className="mascot-line" role="status" aria-live="polite" aria-atomic="true">{model.message}</p>
        <span className="mascot-hint" id="mascot-hint">点一下开心 · 长按摸摸头</span>
      </div>
      <Mascot model={model} desktop={desktop} reducedMotion={reducedMotion} onLaugh={() => dispatch({ type: "laugh" })} onPet={pet} />
    </aside>
  );
}
