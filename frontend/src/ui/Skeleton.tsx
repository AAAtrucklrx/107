export function Skeleton({ label = "正在加载", lines = 3 }: { label?: string; lines?: number }) {
  return <div className="ui-skeleton" role="status" aria-label={label}>
    {Array.from({ length: Math.max(1, Math.min(lines, 8)) }, (_, index) => <span key={index} aria-hidden="true" />)}
  </div>;
}
