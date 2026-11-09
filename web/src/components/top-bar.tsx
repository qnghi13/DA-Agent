import type { ReactNode } from "react";

/** The bar across the top of every signed-in screen: context on the left, actions on the right. */
export function TopBar({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <header className="bg-background/80 supports-[backdrop-filter]:bg-background/70 sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b px-3 backdrop-blur sm:px-4">
      <div className="flex min-w-0 flex-1 items-center gap-2">{left}</div>
      <div className="flex shrink-0 items-center gap-1">{right}</div>
    </header>
  );
}

/** A slash between breadcrumb parts, as in "Workspace / Dataset". */
export function Slash() {
  return (
    <svg viewBox="0 0 16 16" className="text-border size-4 shrink-0" aria-hidden>
      <path d="M10.5 2 5.5 14" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}
