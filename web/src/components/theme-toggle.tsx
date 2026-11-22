import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n/context";
import { browserStorage } from "@/storage";
import { readStoredTheme, resolveTheme, storeTheme, type ThemeChoice } from "@/theme";

const darkQuery = () => window.matchMedia("(prefers-color-scheme: dark)");

/** Put the resolved theme on <html>. Called once before the first render, then on changes. */
export function applyTheme(choice: ThemeChoice = readStoredTheme(browserStorage())): void {
  const theme = resolveTheme(choice, darkQuery().matches);
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function ThemeToggle() {
  const m = useT();
  const [choice, setChoice] = useState<ThemeChoice>(() => readStoredTheme(browserStorage()));

  useEffect(() => {
    applyTheme(choice);
    if (choice !== "system") return;
    const query = darkQuery();
    const follow = () => applyTheme("system");
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, [choice]);

  function choose(value: string) {
    const next = value as ThemeChoice;
    storeTheme(browserStorage(), next);
    setChoice(next);
  }

  const Icon = choice === "dark" ? Moon : choice === "light" ? Sun : Monitor;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={m.theme.label}>
          <Icon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={choice} onValueChange={choose}>
          <DropdownMenuRadioItem value="system">{m.theme.system}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">{m.theme.light}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">{m.theme.dark}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
