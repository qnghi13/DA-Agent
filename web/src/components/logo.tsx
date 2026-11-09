import { cn } from "@/lib/utils";

/** DA-Agent's mark: three rising bars in a rounded square. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <svg viewBox="0 0 24 24" className="size-6" aria-hidden>
        <rect width="24" height="24" rx="6" className="fill-foreground" />
        <rect x="6" y="13" width="3" height="5" rx="1" className="fill-background" />
        <rect x="10.5" y="9.5" width="3" height="8.5" rx="1" className="fill-background" />
        <rect x="15" y="6" width="3" height="12" rx="1" className="fill-background" />
      </svg>
      <span>DA-Agent</span>
    </span>
  );
}
