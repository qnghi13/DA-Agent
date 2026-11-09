import { cn } from "@/lib/utils";
import type { Tone } from "@/status";

const TONES: Record<Tone, string> = {
  success: "border-success-line bg-success-soft text-success",
  info: "border-info-line bg-info-soft text-info",
  warning: "border-warning-line bg-warning-soft text-warning",
  danger: "border-danger-line bg-danger-soft text-danger",
  neutral: "border-border bg-secondary text-muted-foreground",
};

/** A small pill whose colour means the same status everywhere. */
export function StatusBadge({
  tone,
  busy = false,
  className,
  children,
}: {
  tone: Tone;
  busy?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1.5 rounded-full border px-2 text-[11px] font-medium leading-none",
        TONES[tone],
        className,
      )}
    >
      {busy && <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}
