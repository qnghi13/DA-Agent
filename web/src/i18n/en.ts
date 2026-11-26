/** English words for the UI, and the shape every other language must match
 * (docs/design/2026-11-22-vietnamese-ui.md). Values that need data are functions, so word order
 * stays with each language. Data from the user's files is never translated. */

export type Params = Record<string, string>;

/** The values of an analysis headline (web/src/analysis.ts); numbers are already formatted. */
export interface ChangeWords {
  measure: string;
  after: string;
  before: string;
  total: string;
  direction: "up" | "down" | "flat";
  amount: string;
  percent: string | null;
}
export interface TrendWords {
  measure: string;
  direction: string;
  slope: string;
  grain: string;
}
export interface ForecastWords {
  measure: string;
  horizon: string;
  grain: string;
}
export interface CorrelationWords {
  x: string;
  y: string;
  strength: string;
  positive: boolean;
  r: string;
}
export interface DrillWords {
  measure: string;
  segment: string;
  dimension: string;
  before: string;
  after: string;
  next: string;
}
type NoteParams = Record<string, unknown>;

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
    done: "Done",
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
  signIn: {
    title: "Sign in",
    intro: "Open the sign-in link your operator or a teammate sent you. Links work once, for 15 minutes.",
    linkUsed: "This sign-in link was already used or has expired. Ask for a new one.",
    sessionEnded: "Your session has ended. Sign in again.",
    keyRefused: "That API key was not accepted.",
    keyLabel: "Workspace API key",
    checking: "Checking…",
    keyNote: "This browser keeps a session, not the key.",
    withKey: "Sign in with an API key",
    tagline: "Ask questions about your spreadsheets, with answers you can trace to the query.",
    operations: "Operations",
  },
  workspace: {
    openDatasets: "Open datasets",
    hideDatasets: "Hide datasets",
    showDatasets: "Show datasets",
    newDataset: "New dataset",
    datasets: "Datasets",
    noDatasets: "No datasets yet.",
  },
  theme: {
    label: "Theme",
    system: "System",
    light: "Light",
    dark: "Dark",
  },
  setup: {
    stepsLabel: "Dataset setup",
    steps: ["Upload", "Review spec", "Ready"],
  },
  upload: {
    intro:
      "Upload CSV, Excel, or SQLite files, plus optional Markdown or text documents that explain them. The agent proposes how to read them; you review that before asking questions.",
    name: "Name",
    namePlaceholder: "Sales 2026",
    description: "Description",
    optional: "(optional)",
    descriptionPlaceholder:
      "What the data is, business rules, caveats. For example: revenue counts only completed orders.",
    files: "Files",
    dragHere: "Drag files here, or",
    choose: "choose files",
    limits: (files: number, size: string) =>
      `CSV, TSV, Excel, SQLite, Markdown, or text · up to ${files} files, ${size} each`,
    remove: (file: string) => `Remove ${file}`,
    unsupported: (files: string) => `Not a supported file type: ${files}`,
    tooMany: (max: number) => `At most ${max} files.`,
    tooLarge: (files: string) => `Too large: ${files}.`,
    uploading: "Uploading…",
    submit: "Upload and continue",
  },
  spec: {
    title: "Proposed spec",
    intro:
      "How each file becomes a table. Edit names, types, descriptions, and sensitive columns here; everything else in JSON.",
    editAs: "Edit as",
    columns: "Columns",
    notes: "Notes from the proposal",
    asJson: "Spec as JSON",
    needsTables: "the spec needs a tables list",
    invalidJson: (error: string) => `Invalid JSON: ${error}`,
    discardEdits: "Discard edits",
    sending: "Sending…",
    saveAndRebuild: "Save and rebuild",
    approve: "Approve",
    from: (source: string) => `from ${source}`,
    columnCount: (count: number) => `${count} column${count === 1 ? "" : "s"}`,
    description: "Description",
    sourceColumn: "Source column",
    name: "Name",
    type: "Type",
    sensitive: "Sensitive",
    sensitiveHint: "Personal data: the column is left out of the built dataset",
    nameOf: (column: string) => `Name of ${column}`,
    typeOf: (column: string) => `Type of ${column}`,
    descriptionOf: (column: string) => `Description of ${column}`,
    isSensitive: (column: string) => `${column} is sensitive`,
    types: {
      text: "text",
      integer: "integer",
      number: "number",
      money: "money",
      quantity: "quantity",
      date: "date",
      datetime: "datetime",
      boolean: "boolean",
    },
    ruleOps: {
      blank: "blank",
      not_blank: "not blank",
      equals: "equals",
      starts_with: "starts with",
      contains: "contains",
    } as Record<string, string>,
    fillDown: (columns: string) => `Fill down: ${columns}`,
    dedupe: "Exact repeated rows are removed.",
    excludeWhere: (conditions: string) => `Exclude rows where ${conditions}`,
  },
  dataset: {
    isReady: (name: string) => `${name} is ready`,
    readyText:
      "The data is built with the spec you approved. Ask questions in your own words; every answer comes with the queries behind it.",
    ask: "Ask a question",
    statusText: {
      uploaded: "Reading the files and proposing a spec. This can take a minute.",
      proposed: "Review how the files will be read, then approve or edit the spec.",
      building: "Rebuilding the dataset with your edited spec.",
      ready: "",
      failed: "The dataset could not be prepared. The details are below.",
    },
    noDetails: "No details were recorded.",
    filesAndReport: "Files and build report",
    delete: "Delete dataset",
    confirmDelete: (name: string) =>
      `Delete ${name}? Its files, every answer and chat about it, notes, and metrics are deleted for good.`,
  },
  summary: {
    tables: "Tables",
    size: (rows: string, columns: number) => `${rows} rows · ${columns} cols`,
    problems: "Problems to check",
    proposalProblems: "Proposal problems",
    droppedRules: "Exclusion rules dropped",
    deduplicated: "Exact repeats removed",
    files: (count: number) => `Files (${count})`,
    storedAs: (name: string) => `stored as ${name}`,
    documents: (count: number) =>
      count === 1 ? "1 document explains the data." : `${count} documents explain the data.`,
    approvedSpec: "Approved spec",
    filled: (share: string) => `${share} filled`,
    distinct: (count: string) => ` · ${count} distinct`,
    notRead: (count: string, n: number, type: string, samples: string) =>
      `${count} ${n === 1 ? "cell" : "cells"} not read as ${type}${samples ? `, such as ${samples}` : ""}`,
  },
  chart: {
    download: "Download chart",
    downloadAs: (format: string) => `Download ${format}`,
    failed: "This chart could not be drawn.",
  },
  versionStatus: {
    review: "Review",
    discard: "Discard",
    dismiss: "Dismiss",
    versions: "Versions",
    version: (n: number) => `Version ${n}`,
    statuses: {
      uploaded: "uploaded",
      checking: "checking",
      needs_choice: "needs choice",
      proposed: "proposed",
      building: "building",
      applied: "applied",
      failed: "failed",
      discarded: "discarded",
    },
  },
  metricDialog: {
    title: "Save a definition",
    intro: "New questions that use this name are calculated this way, for everyone in the workspace.",
    saved: "Saved. Used from your next question.",
    namePlaceholder: "Revenue",
    aliases: "Other names (comma-separated)",
    aliasesPlaceholder: "Sales, turnover",
    replaces: "Saving a name you already use replaces its old definition.",
    save: "Save definition",
  },
  addFiles: {
    title: "Add files",
    intro: (name: string) =>
      `The new files become the next version of ${name}. Earlier answers keep the data they were made from.`,
    choose: "Choose files to add",
    whatToDo: (file: string) => `What to do with ${file}`,
    add: "Add",
    replace: (file: string) => `Replace ${file}`,
  },
  versionReview: {
    exact: (rows: string) => `${rows} new rows repeat older rows exactly.`,
    sameKey: (rows: string, key: string) => `${rows} new rows have the same ${key || "key"} as older rows.`,
    insideDates: (rows: string, column: string | null) =>
      `${rows} new rows fall inside the dates the old data already covers${column ? ` (${column})` : ""}.`,
    nearMatch: (table: string, extra: number, missing: number) =>
      `Looks like ${table}, but columns differ: +${extra} −${missing}. It will be a separate table.`,
    newColumns: (columns: string) => `New columns: ${columns}.`,
    missingColumns: (columns: string) => `Missing columns: ${columns}.`,
    newTables: "New tables",
    newTablesText: "How the new files become tables. Edit names, types, and descriptions before they are added.",
    repairs: "Proposed fixes to existing tables",
    repairsText:
      "Some values in the new files did not parse. These changes to how the table is read may fix that.",
    parseProblems: "Parse problems",
    noFix: (problem: string) => `The agent could not propose a fix: ${problem}`,
    removed: "Tables that go away",
    notInNewFiles: "is not in the new files.",
    staleMetrics: (names: string) => `Saved definitions that stop working: ${names}.`,
    chooseFor: (tables: string) => `Choose what to do for: ${tables}.`,
    applyAnyway: "Apply anyway",
    apply: "Apply",
    overlapTitle: "new rows overlap the old data",
    empty: "empty",
    overlapChoice: (table: string) => `What to do with the overlap in ${table}`,
    choices: {
      replace: "Replace old file",
      drop_overlap: "Drop overlapping rows",
      keep_both: "Keep both",
    },
    oldFile: "Old file to replace",
    chooseFile: "Choose a file",
    acceptChange: "Accept the change to",
    before: "before:",
    after: "after:",
  },
  evidence: {
    tabs: { steps: "Steps", sql: "SQL", data: "Data", report: "Report" },
    noSteps: "No steps were recorded for this run.",
    checksFound: "What the checks found",
    noQueries: "The agent ran no queries.",
    noResults: "No query results.",
    traceAttempt: (attempt: number) => `Trace, attempt ${attempt}`,
    openTrace: "Open trace",
    queryFailed: "The query failed.",
    query: (n: number) => `Query ${n}`,
    edited: " · edited",
    editQuery: (n: number) => `Edit query ${n}`,
    edit: "Edit",
    copyQuery: (n: number) => `Copy query ${n}`,
    sqlFor: (n: number) => `SQL for query ${n}`,
    running: "Running…",
    run: "Run",
    reset: "Reset",
    editHint: "One SELECT on this dataset. Ctrl+Enter runs it.",
    editedRows: (truncated: boolean, rows: string, n: number) =>
      `Edited query · ${truncated ? "first " : ""}${rows} row${n === 1 ? "" : "s"}`,
    theEditedQuery: "the edited query",
    noRows: "No rows.",
    showingFirst: (n: number, truncated: boolean) =>
      `Showing the first ${n} rows${truncated ? " of a longer result" : ""}.`,
    queryRows: (index: number, truncated: boolean, rows: string, n: number) =>
      `Query ${index} · ${truncated ? "first " : ""}${rows} row${n === 1 ? "" : "s"}`,
    queryLabel: (n: number) => `query ${n}`,
    exportFailed: "The export failed.",
    downloadAs: (label: string) => `Download ${label} as CSV`,
    preparing: "Preparing file…",
    downloadCsv: "Download CSV",
    runLabel: "Run",
    copyRunId: (id: string) => `Copy run id ${id}`,
    copyId: "Copy ID",
    downloadReport: "Download report",
    report: "Run report",
  },
  chat: {
    asOf: (date: string) => `As of ${date}`,
    queued: "Queued",
    running: "Running",
    waiting: "Waiting for a worker",
    working: "Working…",
    planned: (count: number) =>
      count ? `Planned ${count} step${count === 1 ? "" : "s"}` : "Planned the steps",
    checked: "Checked the answer against the data",
    checkFailed: "The checks found problems, so the agent kept working",
    rows: (rows: string, n: number) => ` · ${rows} row${n === 1 ? "" : "s"}`,
    problem: "Problem",
    runFailed: (error: string | null) => `The run failed${error ? `: ${error}` : "."}`,
    needsDetail: "The agent needs more detail:",
    askAgain: "Ask again with that detail in your question.",
    noAnswer: "No answer.",
    showWork: "Show work",
    copy: "Copy",
    correct: "Correct",
    correctHint: "Teach the agent the rule it got wrong; new questions will follow it",
    howCalculated: "How it was calculated",
    openDefinition: "Open memory to see this definition",
    usedDefinition: (name: string) => `Used your definition of ${name}`,
    calculation: (n: number, total: number) => `Calculation ${n} of ${total}`,
    partial: "Some steps can't be put into words yet. Open Show work, then SQL, to see the whole query.",
    saveNamed: (measure: string) => `Save “${measure}” as a definition`,
    saveDefinition: "Save as a definition",
  },
  memory: {
    loading: "Loading notes",
    definitions: (n: number) => `Definitions (${n})`,
    noDefinitions:
      "No definitions yet. Under an answer you trust, open How it was calculated and choose Save as a definition. New questions that use its name are calculated the same way.",
    waiting: (n: number) => `Waiting for review (${n})`,
    reject: "Reject",
    inUse: (n: number) => `In use (${n})`,
    noNotes:
      "No notes yet. When an answer uses the wrong rule, correct it here and new questions will follow the note.",
    history: (n: number) => `History (${n})`,
    teach: "Teach the agent",
    teachText:
      "Notes apply to new questions about this dataset, for everyone in the workspace. A note wins over the dataset's documents where they disagree.",
    kindLabel: "Kind of note",
    kinds: {
      correction: {
        label: "Correction",
        hint: "A business rule the agent got wrong, such as what revenue excludes.",
      },
      alias: { label: "Alias", hint: "A word people use for a column or value." },
      example: { label: "Example", hint: "A question and the SQL that answers it correctly." },
    },
    definition: "Definition",
    question: "Question",
    questionPlaceholder: "The question, such as: Revenue in October 2025",
    notePlaceholder: "Revenue excludes cancelled orders.",
    sqlLabel: "SQL that answers it",
    linked: "Linked to the answer you are correcting.",
    saveNote: "Save note",
    confirmDeleteNote: "Delete this note? New questions will no longer follow it.",
    confirmDeleteDefinition: "Delete this definition? New questions will no longer use it.",
    delete: "Delete",
    rename: "Rename",
    replaced: "Replaced",
    rejected: "Rejected",
    also: (names: string) => ` · also ${names}`,
    stale:
      "The data changed and this definition no longer fits it, so new questions skip it. Save it again from a new answer.",
  },
  conversation: {
    memory: "Memory",
    reviewFiles: "Review new files",
    loadingQuestions: "Loading questions",
    loading: "Loading…",
    showEarlier: "Show earlier questions",
    dataVersion: (n: number) => `data v${n}`,
    about: "About this data",
    data: "Data",
    finishFirst: "Finish or discard the new files first",
    startOver: "Start over: new questions will not refer to the ones above",
    newConversation: "New conversation",
    freshBreak: "New conversation · earlier questions are not remembered",
    runPanel: "How the agent reached this answer",
    memoryPanel: "Notes the agent follows for this dataset",
    versionPanel: "Choices for the files you added",
    divider: (files: string, version: number) =>
      `${files ? `Added ${files}. ` : ""}Answers below use data version ${version}.`,
    details: "Details",
    askAnything: (name: string) => `Ask ${name} anything`,
    askIntro: "Ask in your own words. Every answer comes with the queries behind it, checked against the data.",
    yourQuestion: "Your question",
    placeholder: "Ask a question about this data…",
    asOf: "As of",
    stopping: "Stopping…",
    stop: "Stop",
    stopHint: "Stop this question",
    ask: "Ask",
    sendHint: "Send (Enter) · Shift+Enter for a new line",
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
      analyze: "Ran an analysis",
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
    thanks: "Thanks",
    sayWhy: "Say why",
    good: "Good answer",
    goodHint: "Good answer: keep it as a check for later versions",
    bad: "Bad answer",
    takeBack: "Take back",
    whatWrong: "What was wrong? (optional)",
    reasonsLabel: "Reasons",
    note: "Note",
    notePlaceholder: "Anything else? For example, the right figure.",
    skip: "Skip",
    send: "Send",
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
  analysis: {
    whyChanged: "Why did it change?",
    whyQuestion: (measure: string, from: string, to: string) =>
      `Why did ${measure} change from ${from} to ${to}?`,
    drill: (segment: string, dimension: string) => `See ${segment} by ${dimension}`,
    drillQuestion: (p: DrillWords) =>
      `From ${p.before} to ${p.after}, where did ${p.measure} of ${p.dimension} ${p.segment} change, by ${p.next}?`,
    whereChanged: "Where it changed",
    otherColumns: "By other columns",
    notCause: "This is where it changed, not why. The data does not say why.",
    flat: "The total barely moved; segments offset each other.",
    method: "How it was computed",
    methods: {
      explain_change:
        "The measure summed in each period and split by every value of each column; the parts add up to the change.",
      trend:
        "A straight line fitted through each whole period; a direction only when it is unlikely to be chance.",
      forecast: "Exponential smoothing over the whole periods, with a 95% range.",
      correlation: "Pearson and Spearman correlation over every row.",
      outliers: "A score from the median; values far from it stand out.",
    } as Record<string, string>,
    other: "Other",
    blank: "(blank)",
    status: { new: "new", gone: "gone" } as Record<string, string>,
    share: (value: string) => `${value}% of the change`,
    range: (start: string, end: string) => `${start} to ${end}`,
    grains: {
      day: "day",
      week: "week",
      month: "month",
      quarter: "quarter",
      year: "year",
    } as Record<string, string>,
    changeHeadline: (p: ChangeWords) =>
      p.direction === "flat"
        ? `${p.measure}, ${p.after}: ${p.total}, about the same as ${p.before}.`
        : `${p.measure}, ${p.after}: ${p.total}, ${p.direction} ${p.amount}${p.percent ? ` (${p.percent})` : ""} from ${p.before}.`,
    trendHeadline: (p: TrendWords) =>
      p.direction === "increasing"
        ? `${p.measure} is rising, by about ${p.slope} a ${p.grain}.`
        : p.direction === "decreasing"
          ? `${p.measure} is falling, by about ${p.slope} a ${p.grain}.`
          : `${p.measure} shows no clear trend.`,
    forecastHeadline: (p: ForecastWords) =>
      `${p.measure} for the next ${p.horizon} ${p.grain}(s), with a 95% range.`,
    correlationHeadline: (p: CorrelationWords) =>
      p.strength === "none"
        ? `${p.x} and ${p.y} do not move together (r = ${p.r}).`
        : `${p.x} and ${p.y}: a ${p.strength} ${p.positive ? "positive" : "negative"} correlation (r = ${p.r}).`,
    outliersHeadline: (p: { count: string; measure: string }) => `${p.count} unusual values of ${p.measure}.`,
    notes: {
      unfinished_first: (p: NoteParams) => `${p.period} is left out: the data starts on ${p.data_start}.`,
      unfinished_last: (p: NoteParams) => `${p.period} is left out: the data ends on ${p.data_end}.`,
      missing_periods: (p: NoteParams) => `${p.count} periods in between have no rows.`,
      missing_values: (p: NoteParams) => `${p.count} values of ${p.column} are missing and count as none.`,
      interpolated: (_: NoteParams) => "Missing periods were filled with straight lines between neighbours.",
      rows_without_value: (p: NoteParams) => `${p.count} rows lack a value.`,
      coverage_unknown: (p: NoteParams) => `The data's dates are unknown, so ${p.start} to ${p.end} was used.`,
      flat_total: (_: NoteParams) => "The total barely moved, so no shares are shown.",
      before_zero: (_: NoteParams) => "The earlier total is 0, so there is no percent change.",
      missing_dates: (p: NoteParams) => `${p.count} rows have no ${p.column}; they are left out.`,
    } as Record<string, (p: NoteParams) => string>,
  },
};

export type Messages = typeof en;
