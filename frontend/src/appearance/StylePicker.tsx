import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, Palette } from "lucide-react";
import { useAppearance, type VisualStyle } from "./AppearanceProvider";
import "./family.css";

const styles: { id: VisualStyle; title: string }[] = [
  { id: "frog", title: "奶家口袋" },
  { id: "ustc", title: "科大蓝" },
];

export function StylePicker() {
  const { style, setStyle } = useAppearance();
  return <section className="family-style-settings" aria-labelledby="family-style-title">
    <h2 id="family-style-title">界面风格</h2>
    <div className="family-style-options" role="group" aria-label="界面风格">
      {styles.map((option) => <button key={option.id} className="family-style-option" aria-pressed={style === option.id} type="button" onClick={() => setStyle(option.id)}>
        <span className={`family-swatch family-swatch--${option.id}`} aria-hidden="true" />
        <span>{option.title}</span>
        {style === option.id && <Check size={15} aria-hidden="true" />}
      </button>)}
    </div>
  </section>;
}

/** Retained for callers that offer style selection outside the profile page. */
export function StyleShortcut() {
  const { style, setStyle } = useAppearance();
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger asChild><button className="family-icon-button" type="button" aria-label="切换界面风格" title="切换界面风格"><Palette size={19} aria-hidden="true" /></button></DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Content className="family-menu" sideOffset={8} align="end" collisionPadding={12}>
      <DropdownMenu.Label className="family-menu-label">界面风格</DropdownMenu.Label>
      <DropdownMenu.RadioGroup value={style} onValueChange={(value) => { if (value === "frog" || value === "ustc") setStyle(value); }}>
        {styles.map((option) => <DropdownMenu.RadioItem key={option.id} className="family-menu-item" value={option.id}>
          <span className={`family-swatch family-swatch--${option.id}`} aria-hidden="true" /><span className="family-menu-item-copy">{option.title}</span><DropdownMenu.ItemIndicator><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator>
        </DropdownMenu.RadioItem>)}
      </DropdownMenu.RadioGroup>
    </DropdownMenu.Content></DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
