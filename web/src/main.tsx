import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme } from "./components/theme-toggle";
import { TooltipProvider } from "./components/ui/tooltip";
import { LanguageProvider } from "./i18n/context";
import "./index.css";

applyTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LanguageProvider>
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </LanguageProvider>
  </StrictMode>,
);
