/** English words for the UI, and the shape every other language must match
 * (docs/design/2026-11-22-vietnamese-ui.md). Values that need data are functions, so word order
 * stays with each language. Data from the user's files is never translated. */

export type Params = Record<string, string>;

export const en = {
  locale: "en-US",
  language: {
    label: "Language",
  },
  common: {
    loading: "Loading",
    cancel: "Cancel",
    close: "Close",
    save: "Save",
    saving: "Saving…",
    retry: "Try again",
  },
  account: {
    menu: "Account",
    operator: "Operator",
    workspace: "Workspace",
    operatorSession: "Operator session",
    workspaceMember: "Workspace member",
    inviteOperator: "Invite an operator",
    inviteDevice: "Invite or add a device",
    signOut: "Sign out",
    inviteText: (operator: boolean) =>
      `Anyone who opens the link within 15 minutes signs in to ${operator ? "the operator dashboard" : "this workspace"}. It works once.`,
    link: "Sign-in link",
    copied: "Copied",
    copy: "Copy link",
    creating: "Creating…",
    create: "Create sign-in link",
  },
  errors: {
    signedOut: "You are signed out.",
    slowDown: (text: string) => `Slow down: ${text}`,
    // One entry per API error code (src/da_agent/api/errors.py). A param a message needs and the
    // server did not send makes the web show the server's own words instead.
    codes: {
      not_found: (_: Params) => "Not found.",
      "upload.too_large": (p: Params) => `${p.file} is too large.`,
      "upload.too_many_files": (p: Params) => `At most ${p.max} files.`,
      "upload.unsupported_type": (p: Params) =>
        `${p.file} is not a file type DA-Agent reads. Use one of: ${p.allowed}.`,
      "dataset.not_ready": (_: Params) => "The dataset is not ready yet.",
      "dataset.changed": (_: Params) => "The data changed after this answer. Ask again.",
      "export.expired": (_: Params) => "The export expired. Download it again.",
      "version.already_open": (_: Params) =>
        "Files added earlier are still being checked or reviewed. Finish or discard them first.",
      "version.limit": (p: Params) => `The dataset already has ${p.max} versions, the most it can have.`,
      "limit.sign_in": (_: Params) => "Too many failed sign-ins from this address.",
      "limit.requests": (_: Params) => "Too many requests.",
      "limit.jobs": (_: Params) => "Too many new tasks for this workspace.",
      "limit.budget": (p: Params) => `This workspace has spent today's model budget ($${p.limit}).`,
    },
  },
};

export type Messages = typeof en;
