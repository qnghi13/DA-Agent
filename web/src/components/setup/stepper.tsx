import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

const STEPS = ["Upload", "Review spec", "Ready"] as const;

/** Upload → Review spec → Ready, with the current step marked. */
export function Stepper({ current }: { current: 1 | 2 | 3 }) {
  return (
    <ol className="flex items-center gap-2 text-sm" aria-label="Dataset setup">
      {STEPS.map((label, index) => {
        const step = index + 1;
        const done = step < current;
        const here = step === current;
        return (
          <li key={label} className="flex items-center gap-2" aria-current={here ? "step" : undefined}>
            {index > 0 && <span className={cn("h-px w-6 sm:w-10", done || here ? "bg-foreground/40" : "bg-border")} aria-hidden />}
            <span
              className={cn(
                "grid size-6 shrink-0 place-items-center rounded-full border text-xs font-medium",
                done && "bg-foreground text-background border-foreground",
                here && "border-foreground text-foreground",
                !done && !here && "text-muted-foreground",
              )}
            >
              {done ? <Check className="size-3.5" aria-hidden /> : step}
            </span>
            <span className={cn("whitespace-nowrap", here ? "font-medium" : "text-muted-foreground", !here && "hidden sm:inline")}>
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The page frame for the setup screens: stepper, title, and a short explanation. */
export function SetupPage({
  step,
  title,
  description,
  children,
}: {
  step: 1 | 2 | 3;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-4 py-8 sm:px-6">
        <Stepper current={step} />
        <header className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {description && (
            <p className="text-muted-foreground max-w-2xl text-[15px] leading-relaxed">{description}</p>
          )}
        </header>
        {children}
      </div>
    </div>
  );
}
