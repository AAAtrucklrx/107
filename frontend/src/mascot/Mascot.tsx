import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { mascotAssets, mascotLaughAnimation } from "./assets";
import type { MascotModel } from "./state";

type Gesture = { x: number; y: number; moved: boolean; petted: boolean; rect: DOMRect };
type MascotProps = { model: MascotModel; desktop: boolean; reducedMotion: boolean; onLaugh: () => void; onPet: () => void };
export function Mascot({ model, desktop, reducedMotion, onLaugh, onPet }: MascotProps) {
  const gesture = useRef<Gesture | null>(null);
  const holdTimer = useRef<number | undefined>(undefined);
  const petTimer = useRef<number | undefined>(undefined);
  const clickTimer = useRef<number | undefined>(undefined);
  const suppressClick = useRef(false);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [petting, setPetting] = useState(false);
  const [laughAnimationFailed, setLaughAnimationFailed] = useState(false);
  const animatedImage = model.state === "laugh" && !reducedMotion && !laughAnimationFailed;
  const clearHold = () => window.clearTimeout(holdTimer.current);
  useEffect(() => () => { clearHold(); window.clearTimeout(petTimer.current); window.clearTimeout(clickTimer.current); }, []);
  const start = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    suppressClick.current = false;
    gesture.current = { x: event.clientX, y: event.clientY, moved: false, petted: false, rect: event.currentTarget.getBoundingClientRect() };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    clearHold();
    holdTimer.current = window.setTimeout(() => {
      if (!gesture.current || gesture.current.moved) return;
      gesture.current.petted = true;
      setPetting(true); onPet();
      window.clearTimeout(petTimer.current);
      petTimer.current = window.setTimeout(() => setPetting(false), 180);
    }, 500);
  };
  const move = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const current = gesture.current;
    if (!current) return;
    const x = event.clientX - current.x, y = event.clientY - current.y;
    if (Math.hypot(x, y) <= 8) return;
    current.moved = true; clearHold();
    if (!desktop || current.petted) return;
    setDragging(true);
    const rect = current.rect;
    setOffset({
      x: Math.min(window.innerWidth - rect.right - 8, Math.max(-rect.left + 8, x)),
      y: Math.min(window.innerHeight - rect.bottom - 8, Math.max(-rect.top + 8, y)),
    });
  };
  const finish = (event: ReactPointerEvent<HTMLButtonElement>, cancelled = false) => {
    const current = gesture.current;
    clearHold(); gesture.current = null;
    suppressClick.current = cancelled || Boolean(current?.moved || current?.petted);
    // The browser click follows pointerup synchronously; keyboard activation remains available afterward.
    clickTimer.current = window.setTimeout(() => { suppressClick.current = false; }, 0);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDragging(false); setOffset({ x: 0, y: 0 });
  };
  return (
    <div className={`mascot-motion${dragging ? " is-dragging" : ""}`} style={{ transform: `translate(${offset.x}px, ${offset.y}px)` }}>
      <button className={`mascot-button${petting ? " is-petting" : ""}`} type="button" aria-label="奶蛙" aria-describedby="mascot-hint"
        data-state={model.state} data-animated-image={String(animatedImage)} onPointerDown={start} onPointerMove={move} onPointerUp={(event) => finish(event)} onPointerCancel={(event) => finish(event, true)}
        onClick={() => { if (suppressClick.current) { suppressClick.current = false; return; } onLaugh(); }}
        onContextMenu={(event) => event.preventDefault()}>
        <img className="mascot-image" src={animatedImage ? mascotLaughAnimation : mascotAssets[model.state]}
          onError={animatedImage ? () => setLaughAnimationFailed(true) : undefined}
          alt="" aria-hidden="true" draggable={false} width={animatedImage ? 160 : 1024} height={animatedImage ? 160 : 1024} decoding="async" />
      </button>
    </div>
  );
}
