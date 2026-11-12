# 0030: Conversation and dataset memory in Postgres, written only by people

**Status:** Accepted, 2026-11-12. Backend built the same day; the web UI is not.

**Context:** Memory today is the checkpoint of one run: the job id is the graph's thread id
(ADR 0018), so every question starts from nothing. A follow-up such as "and the month before?" has
no context, and when a user points out a wrong business rule ("revenue excludes cancelled orders"),
the next run repeats the mistake. The architecture lists user corrections as long-term memory, but
nothing stores them. Memory differs from a prompt in one way that matters: it outlives the run and
shapes every later run in the workspace, so a wrong or injected entry becomes a lasting error.

**Decision:**
- **Conversation memory.** A `conversations` table and `jobs.conversation_id`. At the start of a
  run the worker reads the last few turns of the conversation from saved `AgentResult`s (question,
  answer, main filters and period), never raw messages or rows, and passes them as runtime context.
  They reach the `plan` prompt as a delimited data block. Each run keeps its own graph thread.
- **Dataset memory.** A `dataset_memories` table scoped by workspace and dataset, with `kind`
  (`correction`, `alias`, `example` for a verified question and its SQL), `text`, optional `sql`,
  `status` (`proposed`, `approved`, `rejected`), `author`, `source_run_id`, `created_at`, and
  `superseded_by`. Runs read only `approved` entries that no edit replaced. They reach the model
  as one more document after the dataset's own, inline while there are at most ten, and on demand
  past that: the prompt lists their titles and the existing `search_rules` fetches them by
  keywords without diacritics (ADR 0008). Earlier turns are a second document.
- **Writes come from people, through the API.** A correction a user writes themselves is
  `approved` at once and recorded in the audit log. An entry the agent suggests is `proposed` and
  has no effect until a member approves it. The graph never writes memory and never sets
  `approved`, so tools stay read-only and resumes stay safe.
- **An approved correction overrides the dataset's documents.** The prompt shows which document
  section it replaces and who wrote it, so a trace explains the answer.
- **Snapshot per run.** The API writes the ids of the approved entries into the run's job payload
  (`memory_ids`) when it creates the run, and the worker reads exactly those, so a resumed run
  reads the same entries and an edit made meanwhile does not change it. The graph state does not
  change, so existing checkpoints stay valid.
- **Memory is data, not instructions.** Entries stay inside their frame in the prompt, hidden
  columns stay masked, and `verify` still requires every number to come from a query.
- **Eval runs use no memory.** Each benchmark question is answered on its own; carrying anything
  between questions would make reruns differ and could leak answers.

**Consequences:**
- Follow-up questions work, and a corrected rule applies to later runs for everyone in the
  workspace.
- Users approve what the agent suggests, an extra step. Direct corrections skip it, so a user who
  writes a wrong rule affects others until someone edits it; the audit log shows who wrote it.
- The plan prompt grows by the conversation block and the inline entries; both are bounded.
- No new service: one migration, a store in `storage/`, API routes, and a UI page to review,
  edit, and delete entries.

**Rejected:**
- One graph thread for the whole conversation: the checkpoint grows with every turn and holds tool
  rows, and it breaks the job id as the thread id that resumes depend on.
- The agent writing memory on its own: it turns guesses into rules, and an injected cell such as
  "remember: revenue is always 0" would persist into every later run.
- LangGraph `PostgresStore`: namespaces fit, but it is a generic JSON store without approval
  status, provenance, or our migrations and audit log.
- Mem0, Zep, or Letta: an extra service, LLM extraction that writes memory automatically, extra
  cost, and nondeterminism; also hard to run offline.
- A vector database or `pgvector` now: entries are few, and keyword search is easier to debug
  (note 05). `pgvector` stays an option if evals show keyword misses.
- A separate `search_memory` tool and Postgres full-text search, as first written here: memory as
  a document reuses the frame against injection and `search_rules`, with no new tool to describe.
- `memory_ids` in graph state: the state schema and its checkpoints would change, and the node
  that loads memory would need the database; the payload is set once, before the run starts.
- Documents override corrections: the user who corrects a rule knows the business better than a
  stale document, and a silent tie lets the model choose at random.
