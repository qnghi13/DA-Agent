import { Check, Copy, LogOut, UserPlus } from "lucide-react";
import { useState } from "react";
import { createLoginLink } from "@/api";
import type { Me } from "@/auth";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n/context";
import { errorMessage } from "@/i18n/errors";

function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

/** Who is signed in, with invite and sign-out. */
export function AccountMenu({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const m = useT();
  const [inviting, setInviting] = useState(false);
  const name = me.role === "operator" ? m.account.operator : (me.workspace_name ?? m.account.workspace);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="rounded-full" aria-label={m.account.menu}>
            <span className="grid size-7 place-items-center rounded-full bg-gradient-to-br from-neutral-700 to-neutral-950 text-[11px] font-semibold text-white dark:from-neutral-200 dark:to-neutral-500 dark:text-neutral-950">
              {initials(name)}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuLabel className="font-normal">
            <div className="text-sm font-medium">{name}</div>
            <div className="text-muted-foreground text-xs">
              {me.role === "operator" ? m.account.operatorSession : m.account.workspaceMember}
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setInviting(true)}>
            <UserPlus />
            {me.role === "operator" ? m.account.inviteOperator : m.account.inviteDevice}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onSignOut}>
            <LogOut />
            {m.account.signOut}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <InviteDialog role={me.role} open={inviting} onOpenChange={setInviting} />
    </>
  );
}

function InviteDialog({
  role,
  open,
  onOpenChange,
}: {
  role: Me["role"];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const m = useT();
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function create() {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      setLink((await createLoginLink()).url);
    } catch (caught) {
      setError(errorMessage(caught, m));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function change(next: boolean) {
    if (!next) {
      setLink(null);
      setError(null);
      setCopied(false);
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={change}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {role === "operator" ? m.account.inviteOperator : m.account.inviteDevice}
          </DialogTitle>
          <DialogDescription>{m.account.inviteText(role === "operator")}</DialogDescription>
        </DialogHeader>
        {link && (
          <Input
            readOnly
            value={link}
            aria-label={m.account.link}
            className="font-mono text-xs"
            onFocus={(event) => event.target.select()}
          />
        )}
        {error && (
          <p role="alert" className="text-danger text-sm">
            {error}
          </p>
        )}
        <DialogFooter>
          {link ? (
            <Button onClick={copy}>
              {copied ? <Check /> : <Copy />}
              {copied ? m.account.copied : m.account.copy}
            </Button>
          ) : (
            <Button onClick={create} disabled={busy}>
              {busy ? m.account.creating : m.account.create}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
