import { GraduationCap } from "lucide-react";
import { familyCharacters, useAppearance } from "../appearance/AppearanceProvider";
import "../appearance/family.css";

export function Brand({ compact = false }: { compact?: boolean }) {
  const { style, character } = useAppearance();
  const current = familyCharacters.find((item) => item.id === character) ?? familyCharacters[0];
  return <div className={`brand family-brand ${compact ? "brand--compact family-brand--compact" : ""}`} aria-label="小蛙助手">
    <span className="family-brand-logo" aria-hidden="true">{style === "frog" ? <img src={current.src} alt="" width="28" height="34" /> : <GraduationCap size={23} strokeWidth={1.6} />}</span>
    <span className="family-brand-text"><strong>小蛙助手</strong>{!compact && <span className="family-brand-descriptor">科大校园助手</span>}</span>
  </div>;
}
