export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? "brand--compact" : ""}`} aria-label="小蜗科大学术工作台">
      <span className="brand__logo" aria-hidden="true">蜗</span>
      <span className="brand__text">
        <strong>小蜗</strong>
        {!compact && <span className="brand__descriptor">科大校园智能助手</span>}
      </span>
    </div>
  );
}
