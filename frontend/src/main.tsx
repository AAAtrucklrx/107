import React from "react";
import ReactDOM from "react-dom/client";
import "./catalog.css";
import "./styles/tokens.css";
import "./ui/ui.css";
import { App } from "./App";
import "./appearance/appearance.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
