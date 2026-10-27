export type MascotEventType = "think" | "success" | "error" | "idle" | "eat";
export type MascotEvent = { type: MascotEventType; message?: string };
export const MASCOT_EVENT = "xiaowo:mascot";
export function emitMascot(type: MascotEventType, options: { message?: string } = {}) {
  window.dispatchEvent(new CustomEvent<MascotEvent>(MASCOT_EVENT, { detail: { type, ...options } }));
}
