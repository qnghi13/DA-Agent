import { useEffect, useState } from "react";

const isDark = () => document.documentElement.classList.contains("dark");

/** Whether the page shows the dark theme now; follows the theme toggle and the system. */
export function useDark(): boolean {
  const [dark, setDark] = useState(isDark);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(isDark()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}
