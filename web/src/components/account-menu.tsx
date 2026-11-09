import { Check, Copy, LogOut, UserPlus } from "lucide-react";
import { useState } from "react";
import { createLoginLink, errorMessage } from "@/api";
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
  const [inviting, setInviting] = useState(false);
  const name = me.role === "operator" ? "Operator" : (me.workspace_name ?? "Workspace");
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="rounded-full" aria-label="Account">
            <span className="grid size-7 place-items-center rounded-full bg-gradient-to-br from-neutral-700 to-neutral-950 text-[11px] font-semibold text-white dark:from-neutral-200 dark:to-neutral-500 dark:text-neutral-950">
              {initials(name)}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuLabel className="font-normal">
            <div className="text-sm font-medium">{name}</div>
            <div className="text-muted-foreground text-xs">
              {me.role === "operator" ? "Operator session" : "Workspace member"}
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setInviting(true)}>
            <UserPlus />
            {me.role === "operator" ? "Invite an operator" : "Invite or add a device"}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onSignOut}>
            <LogOut />
            Sign out
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
      setError(errorMessage(caught));
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
            {role === "operator" ? "Invite an operator" : "Invite or add a device"}
          </DialogTitle>
          <DialogDescription>
            Anyone who opens the link within 15 minutes signs in to{" "}
            {role === "operator" ? "the operator dashboard" : "this workspace"}. It works once.
          </DialogDescription>
        </DialogHeader>
        {link && (
          <Input
            readOnly
            value={link}
            aria-label="Sign-in link"
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
              {copied ? "Copied" : "Copy link"}
            </Button>
          ) : (
            <Button onClick={create} disabled={busy}>
              {busy ? "Creating…" : "Create sign-in link"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
