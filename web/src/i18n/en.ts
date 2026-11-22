/** English words for the UI, and the shape every other language must match
 * (docs/design/2026-11-22-vietnamese-ui.md). Values that need data are functions, so word order
 * stays with each language. Data from the user's files is never translated. */

export type Params = Record<string, string>;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const AGGREGATES: Record<string, string> = {
  sum: "total of",
  avg: "average",
  min: "lowest",
  max: "highest",
  median: "median",
};

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
  explain: {
    date: (day: number, month: number, year: number) => `${day} ${MONTHS[month - 1]} ${year}`,
    units: {
      day: "day",
      month: "month",
      year: "year",
      quarter: "quarter",
      hour: "hour of day",
      weekday: "day of week",
      month_of_year: "month of year",
    } as Record<string, string>,
    rows: "rows",
    allColumns: "all columns",
    count: (of: string | null, distinct: boolean) =>
      of ? `number of ${distinct ? "different " : ""}${of}` : "number of rows",
    aggregate: (func: string, of: string, distinct: boolean) =>
      `${AGGREGATES[func] ?? func} ${distinct ? "different " : ""}${of}`,
    where: (text: string, conditions: string) => `${text} where ${conditions}`,
    and: " and ",
    or: " or ",
    matches: {
      ignore_case: " (ignoring case)",
      ignore_accents: " (ignoring accents)",
      ignore_case_and_accents: " (ignoring case and accents)",
    } as Record<string, string>,
    comparisons: {
      equals: "is",
      not_equals: "is not",
      greater: "is greater than",
      at_least: "is at least",
      less: "is less than",
      at_most: "is at most",
      starts_with: "starts with",
      ends_with: "ends with",
      contains: "contains",
    } as Record<string, string>,
    period: (subject: string, from: string, to: string) => `${subject} from ${from} to ${to}`,
    onOrAfter: (subject: string, from: string) => `${subject} on or after ${from}`,
    onOrBefore: (subject: string, to: string) => `${subject} on or before ${to}`,
    is: (subject: string, values: string) => `${subject} is ${values}`,
    isNot: (subject: string, values: string) => `${subject} is not ${values}`,
    between: (subject: string, low: string, high: string) => `${subject} is between ${low} and ${high}`,
    empty: (subject: string) => `${subject} is empty`,
    notEmpty: (subject: string) => `${subject} is not empty`,
    isYes: (subject: string) => `${subject} is yes`,
    isNo: (subject: string) => `${subject} is no`,
    matchedWith: (table: string, on: string) => `matched with ${table}${on ? ` on ${on}` : ""}`,
    keptUnmatched: " (rows without a match kept)",
    top: (descending: boolean, limit: number, by: string) =>
      `${descending ? "top" : "bottom"} ${limit} by ${by}`,
    sorted: (by: string, descending: boolean) =>
      `sorted by ${by}, ${descending ? "highest" : "lowest"} first`,
    firstRows: (limit: number) => `the first ${limit} rows`,
    eachCombinationOnce: (columns: string) => `${columns}, each combination once`,
    labels: {
      data: "Data",
      calculated: "Calculated",
      shows: "Shows",
      rowsWhere: "Only rows where",
      groupedBy: "Grouped by",
      groupsWhere: "Only groups where",
      shown: "Shown",
    },
  },
  metrics: {
    onlyRowsWhere: (measure: string, filters: string) => `${measure}, only rows where ${filters}`,
    nameMissing: "Give the definition a name.",
    nameTooLong: (max: number) => `A name has at most ${max} characters.`,
    tooManyAliases: (max: number) => `At most ${max} other names.`,
    aliasTooLong: (max: number) => `Other names have at most ${max} characters each.`,
  },
  run: {
    planning: "Planning",
    checking: "Checking the answer",
    querying: (count: number) => `Querying the data · ${count} ${count === 1 ? "query" : "queries"}`,
    reading: "Reading the data",
    queries: (count: number) => `${count} ${count === 1 ? "query" : "queries"}`,
    stopped: "Stopped",
    failed: "Failed",
    verified: "Verified",
    needsDetail: "Needs detail",
    noAnswer: "No answer",
    yes: "Yes",
    no: "No",
    tools: {
      describe_dataset: "Read the table descriptions",
      run_sql: "Queried the data",
      make_chart: "Drew a chart",
      search_rules: "Looked up the business rules",
      submit_answer: "Proposed an answer",
    } as Record<string, string>,
    stopReasons: {
      answered: "Answered",
      needs_clarification: "The agent needs more detail to answer.",
      iteration_limit: "Stopped after too many steps without an answer.",
      budget_exceeded: "Stopped: this question reached its spending limit.",
      llm_error: "The language model could not be reached.",
      invalid_tool_call: "The model kept making invalid tool calls.",
      no_tool_call: "The model stopped without answering.",
      cancelled: "You stopped this question.",
      deadline_exceeded: "Stopped: this question ran out of time.",
    } as Record<string, string>,
  },
  feedback: {
    reasons: {
      wrong_number: "Wrong number",
      misunderstood: "Answered a different question",
      should_abstain: "Should have said it can't tell",
      unclear: "Hard to understand",
      other: "Something else",
    },
  },
  status: {
    uploaded: "Reading",
    proposed: "Review",
    building: "Building",
    ready: "Ready",
    failed: "Failed",
  },
  profile: {
    mean: (value: string) => ` · mean ${value}`,
    noRepeats: (examples: string) => `no value repeats, such as ${examples}`,
  },
  suggestions: {
    generic: [
      "What are the totals by month?",
      "Which items appear most often?",
      "What date range does the data cover?",
    ],
    totalByMonth: (amount: string) => `What is the total ${amount} by month?`,
    total: (amount: string) => `What is the total ${amount}?`,
    top5: (category: string, amount: string) => `Top 5 ${category} by total ${amount}`,
    countByStatus: (status: string) => `How many records are there for each ${status}?`,
    bestMonth: (amount: string) => `Which month had the highest total ${amount}?`,
    dateRange: (date: string) => `What date range does ${date} cover?`,
  },
  versions: {
    stuck: "Checking the new files stopped before it finished. Discard them and add them again.",
    checking: "Checking new files…",
    needsDecision: "Your new files need a decision",
    adding: "Adding the new files…",
    failed: (error: string | null) => `Couldn't add files: ${error ?? "no details were recorded"}`,
    discarded: "Discarded the new files",
    tables: (count: number) => `${count} tables`,
    added: (rows: string, table: string, rate: string | null) =>
      `Added ${rows} rows to ${table}${rate === null ? "" : ` (parse rate ${rate}%)`}`,
    earlierNewFiles: (files: string) =>
      `Some of these rows are also in ${files}, sent in this same upload.`,
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
