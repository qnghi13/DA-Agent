import { Languages } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useLanguage, useT } from "@/i18n/context";
import { isLanguage, LANGUAGES } from "@/i18n/language";

/** English or Tiếng Việt, next to the theme toggle, so it is there before signing in too. */
export function LanguageToggle() {
  const { language, setLanguage } = useLanguage();
  const m = useT();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={m.language.label}>
          <Languages className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={language}
          onValueChange={(value) => isLanguage(value) && setLanguage(value)}
        >
          {LANGUAGES.map((item) => (
            <DropdownMenuRadioItem key={item.value} value={item.value} lang={item.value}>
              {item.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
