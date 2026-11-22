import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { en, type Messages } from "./en";
import { browserStorage } from "../storage";
import { applyLanguage, type Language, messagesFor, readStoredLanguage, storeLanguage } from "./language";

interface LanguageState {
  language: Language;
  setLanguage: (language: Language) => void;
  m: Messages;
}

const LanguageContext = createContext<LanguageState>({
  language: "en",
  setLanguage: () => {},
  m: en,
});

/** The UI language for everything below it. Text is worked out at render time from codes, so a
 * switch changes what is on screen without a reload. */
export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setChoice] = useState<Language>(() => readStoredLanguage(browserStorage()));

  useEffect(() => applyLanguage(language, document), [language]);

  const value = useMemo<LanguageState>(
    () => ({
      language,
      m: messagesFor(language),
      setLanguage: (next) => {
        storeLanguage(browserStorage(), next);
        setChoice(next);
      },
    }),
    [language],
  );
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): Pick<LanguageState, "language" | "setLanguage"> {
  return useContext(LanguageContext);
}

/** The current dictionary. */
export function useT(): Messages {
  return useContext(LanguageContext).m;
}
