import type { ReactNode } from "react";

export function Page({ title, description, action, children, className = "" }: {
  title: string; description?: string; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return <section className={`ui-page ${className}`}>
    <header className="ui-page__heading"><div><h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</header>
    {children}
  </section>;
}
