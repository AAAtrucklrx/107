import { useEffect, useState, type ReactNode } from "react";
import "../styles/shell.css";

export type ViewportForm = "phone" | "tablet" | "desktop";

function viewportForm(): ViewportForm {
  const width = window.innerWidth;
  return width < 640 ? "phone" : width <= 1024 ? "tablet" : "desktop";
}

export function useViewportForm(): ViewportForm {
  const [form, setForm] = useState(viewportForm);
  useEffect(() => {
    const update = () => setForm(viewportForm());
    window.addEventListener("resize", update);
    update();
    return () => window.removeEventListener("resize", update);
  }, []);
  return form;
}

export function AppScaffold({ form, header, navigation, children, native = false }: {
  form: ViewportForm;
  header: ReactNode;
  navigation: ReactNode;
  children: ReactNode;
  native?: boolean;
}) {
  return <div className="app-shell app-scaffold" data-form={form} data-native={native || undefined}>
    <header className="scaffold-header">{header}</header>
    {form !== "phone" && <aside className="scaffold-rail">{navigation}</aside>}
    <div className="scaffold-content">{children}</div>
    {form === "phone" && navigation}
  </div>;
}
