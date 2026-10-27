import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import { familyCharacters, useAppearance } from "./AppearanceProvider";
import "./family.css";

export function FamilyShortcut() {
  const { character, setCharacter } = useAppearance();
  const current = familyCharacters.find((item) => item.id === character) ?? familyCharacters[0];
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger asChild><button type="button" className="family-shortcut" aria-label={`切换奶家人，当前${current.name}`}>
      <img src={current.src} alt="" width="24" height="30" /><span>{current.name}</span><ChevronDown size={14} aria-hidden="true" />
    </button></DropdownMenu.Trigger>
    <DropdownMenu.Portal><DropdownMenu.Content className="family-menu" sideOffset={8} align="end" collisionPadding={12}>
      <DropdownMenu.Label className="family-menu-label">今天谁陪你？</DropdownMenu.Label>
      <DropdownMenu.RadioGroup value={character} onValueChange={(value) => {
        if (value === "frog" || value === "egg" || value === "mouse") setCharacter(value);
      }}>
        {familyCharacters.map((item) => <DropdownMenu.RadioItem key={item.id} className="family-menu-item" value={item.id}>
          <img className="family-menu-image" src={item.src} alt="" width="32" height="38" />
          <span className="family-menu-item-copy">{item.name}</span>
          <DropdownMenu.ItemIndicator><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator>
        </DropdownMenu.RadioItem>)}
      </DropdownMenu.RadioGroup>
    </DropdownMenu.Content></DropdownMenu.Portal>
  </DropdownMenu.Root>;
}

export function FamilyPicker() {
  const { character, setCharacter } = useAppearance();
  return <section className="family-settings" aria-labelledby="family-picker-title">
    <h2 id="family-picker-title">奶家人</h2>
    <div className="family-options" role="group" aria-label="选择奶家人">
      {familyCharacters.map((item) => <button type="button" className="family-option" aria-pressed={character === item.id} onClick={() => setCharacter(item.id)} key={item.id}>
        <span className="family-option-art"><img src={item.src} alt="" width="80" height="96" /></span>
        <span>{item.name}</span>
        {character === item.id && <Check className="family-option-check" size={14} aria-hidden="true" />}
      </button>)}
    </div>
  </section>;
}
