import wordmark from "../assets/xiaowo-wordmark.svg";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "brand--compact" : ""}`} aria-label="小蜗科大学术工作台">
      <img src={wordmark} alt="小蜗" className="brand__mark" />
      {!compact && <span className="brand__descriptor">科大学术工作台</span>}
    </div>
  );
}
