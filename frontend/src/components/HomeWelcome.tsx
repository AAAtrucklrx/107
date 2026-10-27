import { familyCharacters, useAppearance } from "../appearance/AppearanceProvider";

export function HomeWelcome({ personal, greeting = "今天过得怎么样", name, date }: {
  personal: boolean; greeting?: string; name?: string; date?: string;
}) {
  const { style, character, setCharacter } = useAppearance();
  const selected = familyCharacters.find((member) => member.id === character) ?? familyCharacters[0];
  return <div className={`home-intro home-intro--${style}`}>
    <p className="home-eyebrow">{greeting}{name ? `，${name}` : ""}{date && <span> · {date}</span>}</p>
    <h1>{style === "frog" ? "奶家人，今天谁陪你？" : "今天想了解什么？"}</h1>
    {style === "frog" ? <>
      <div className="family-stage" role="group" aria-label="选择陪伴角色">
        {familyCharacters.map((member) => <button
          key={member.id}
          className={`family-member${member.id === "frog" ? " family-member--primary" : ""}`}
          type="button"
          aria-label={`让${member.name}陪我聊`}
          aria-pressed={character === member.id}
          onClick={() => setCharacter(member.id)}
        >
          <img src={member.src} alt="" width="90" height="164" fetchPriority={member.id === "frog" ? "high" : "auto"} />
          <span>{member.name}</span>
        </button>)}
      </div>
      <p className="family-line" aria-live="polite">{selected.line}</p>
    </> : <p className="home-description">{personal ? "从今天的课开始，或聊聊校园日常。" : "校园日常，随时来问。"}</p>}
  </div>;
}
