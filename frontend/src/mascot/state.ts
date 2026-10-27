import type { MascotEvent } from "./events";
import { mascotLines, randomLine } from "./lines";

export type MascotState = "idle" | "laugh" | "think" | "sleep" | "celebrate" | "surprised" | "eat";
export type MascotModel = { state: MascotState; message: string; busy: boolean; revision: number };
export type MascotAction =
  | { type: "event"; event: MascotEvent }
  | { type: "laugh"; message?: string }
  | { type: "pet" }
  | { type: "sleep" | "wake" | "settle" };
export const initialMascot: MascotModel = { state: "idle", message: mascotLines.idle, busy: false, revision: 0 };
export const reactionDuration: Partial<Record<MascotState, number>> = { laugh: 1500, celebrate: 3000, surprised: 3000, eat: 3000 };

export function mascotReducer(model: MascotModel, action: MascotAction): MascotModel {
  const next = (state: MascotState, message: string, busy = model.busy): MascotModel => ({ state, message, busy, revision: model.revision + 1 });
  if (action.type === "event") {
    const { type, message } = action.event;
    if (type === "think") return next("think", message || mascotLines.think, true);
    if (type === "success") return next("celebrate", message || randomLine(mascotLines.success), false);
    if (type === "error") return next("surprised", message || mascotLines.error, false);
    if (type === "eat") return model.busy ? model : next("eat", message || mascotLines.eat, false);
    return next("idle", message || mascotLines.idle, false);
  }
  if (action.type === "laugh") return next("laugh", action.message || randomLine(mascotLines.laugh));
  if (action.type === "pet") return next(model.state === "sleep" ? "idle" : model.state, "摸摸头，好感度 +1");
  if (action.type === "sleep") return model.busy ? model : next("sleep", mascotLines.sleep);
  if (action.type === "wake") return model.state === "sleep" ? next("idle", mascotLines.idle) : model;
  return model.busy ? next("think", mascotLines.think) : next("idle", mascotLines.idle);
}
