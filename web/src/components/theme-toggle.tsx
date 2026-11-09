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
import { readStoredTheme, resolveTheme, storeTheme, type ThemeChoice } from "@/theme";

const darkQuery = () => window.matchMedia("(prefers-color-scheme: dark)");

/** Put the resolved theme on <html>. Called once before the first render, then on changes. */
export function applyTheme(choice: ThemeChoice = readStoredTheme(window.localStorage)): void {
  const theme = resolveTheme(choice, darkQuery().matches);
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>(() => readStoredTheme(window.localStorage));

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
    storeTheme(window.localStorage, next);
    setChoice(next);
  }

  const Icon = choice === "dark" ? Moon : choice === "light" ? Sun : Monitor;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          <Icon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={choice} onValueChange={choose}>
          <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
