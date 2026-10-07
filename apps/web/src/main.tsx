import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { initializeTheme } from "./lib/theme";
import "./styles/base.css";

initializeTheme();

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("Root element is missing");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
