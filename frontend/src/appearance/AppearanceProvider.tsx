import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";
import frog from "./assets/naiwa-main.png";
import egg from "./assets/naidan-family.png";
import mouse from "./assets/naishu-family.png";

export { default as familyLaughSrc } from "./assets/naiwa-laugh-hub.gif";

export type VisualStyle = "frog" | "ustc";
export type FamilyCharacterId = "frog" | "egg" | "mouse";
export const familyCharacters: readonly { id: FamilyCharacterId; name: string; src: string; line: string }[] = [
  { id: "frog", name: "奶蛙", src: frog, line: "今天也开心一点" },
  { id: "egg", name: "奶蛋", src: egg, line: "慢慢来，没关系" },
  { id: "mouse", name: "奶鼠", src: mouse, line: "一起找找答案" },
];
const preferenceKey = "xiaowa-visual-style";
const characterKey = "xiaowa-family-character";
function savedStyle(): VisualStyle {
  try { return localStorage.getItem(preferenceKey) === "ustc" ? "ustc" : "frog"; }
  catch { return "frog"; }
}
function savedCharacter(): FamilyCharacterId {
  try {
    const saved = localStorage.getItem(characterKey);
    return saved === "egg" || saved === "mouse" ? saved : "frog";
  } catch { return "frog"; }
}
const AppearanceContext = createContext<{
  style: VisualStyle;
  setStyle: (style: VisualStyle) => void;
  character: FamilyCharacterId;
  setCharacter: (character: FamilyCharacterId) => void;
}>({ style: "frog", setStyle: () => {}, character: "frog", setCharacter: () => {} });
export const useAppearance = () => useContext(AppearanceContext);

/** Visual preferences only. Switching style never remounts the session or chat. */
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [style, setStyle] = useState<VisualStyle>(savedStyle);
  const [character, setCharacter] = useState<FamilyCharacterId>(savedCharacter);
  useLayoutEffect(() => {
    document.documentElement.dataset.style = style;
    try { localStorage.setItem(preferenceKey, style); } catch { /* Appearance also works without persistent storage. */ }
  }, [style]);
  useLayoutEffect(() => {
    try { localStorage.setItem(characterKey, character); } catch { /* Character selection remains usable without storage. */ }
  }, [character]);
  return <AppearanceContext.Provider value={{ style, setStyle, character, setCharacter }}>{children}</AppearanceContext.Provider>;
}
