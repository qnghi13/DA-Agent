import { AlertCircle, BookMarked, Check, CircleHelp, Copy, Loader2, PanelRight, X } from "lucide-react";
import { lazy, Suspense, useEffect, useReducer, useRef, useState } from "react";
import { type Api, type Run, errorMessage } from "@/api";
import { DataTable } from "@/components/data-table";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  type Trail,
  type TrailStep,
  activity,
  answerSummary,
  formatAnswer,
  initialTrail,
  reduceTrail,
  stopReasonText,
  toolLabel,
} from "@/run";
import type { SseEvent } from "@/sse";

// Vega is large; load it only when a chart is on screen.
const Chart = lazy(() => import("@/Chart"));

export interface Evidence {
  run: Run;
  trail: Trail;
}

/** One question and the agent's reply: live progress while it runs, then the answer. */
export function RunMessage({
  api,
  runId,
  active,
  onShowWork,
  onCorrect,
}: {
  api: Api;
  runId: string;
  active: boolean;
  onShowWork: (evidence: Evidence, trigger: HTMLElement) => void;
  /** Opens memory to write a correction linked to this run. */
  onCorrect?: (run: Run, trigger: HTMLElement) => void;
}) {
  const [run, setRun] = useState<Run | null>(null);
  const [trail, dispatch] = useReducer(
    (state: Trail, event: SseEvent | "reset") =>
      event === "reset" ? initialTrail() : reduceTrail(state, event),
    undefined,
    initialTrail,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    dispatch("reset");
    (async () => {
      try {
        setRun(await api.getRun(runId));
        await api.streamRunEvents(runId, dispatch, controller.signal);
        if (!controller.signal.aborted) setRun(await api.getRun(runId));
      } catch (caught) {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      }
    })();
    return () => controller.abort();
  }, [api, runId]);

  if (!run) {
    return error ? (
      <p role="alert" className="text-danger text-sm">
        {error}
      </p>
    ) : (
      <div className="bg-muted h-16 animate-pulse rounded-xl" aria-label="Loading" />
    );
  }

  const finished = run.status === "succeeded" || run.status === "failed";
  return (
    <article className="flex flex-col gap-3" aria-busy={!finished && !error}>
      <div className="flex justify-end">
        <div className="bg-secondary max-w-[85%] rounded-2xl rounded-br-md border px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap">
          {run.question}
          <div className="text-muted-foreground mt-1 text-right text-[11px]">As of {run.as_of}</div>
        </div>
      </div>
      {finished ? (
        <Answer
          run={run}
          trail={trail}
          active={active}
          onShowWork={(trigger) => onShowWork({ run, trail }, trigger)}
          onCorrect={onCorrect && ((trigger) => onCorrect(run, trigger))}
        />
      ) : (
        <Progress
          trail={trail}
          queued={run.status === "queued" && trail.steps.length === 0 && trail.turns === 0}
        />
      )}
      {error && (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      )}
    </article>
  );
}

function useElapsed(): number {
  const started = useRef(Date.now());
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return Math.max(0, Math.round((now - started.current) / 1000));
}

function Progress({ trail, queued }: { trail: Trail; queued: boolean }) {
  const elapsed = useElapsed();
  return (
    <div className="flex flex-col gap-2" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm">
        <StatusBadge tone="info" busy>
          {queued ? "Queued" : "Running"}
        </StatusBadge>
        <span className="text-foreground font-medium">
          {queued ? "Waiting for a worker" : activity(trail)}
        </span>
        <span className="text-muted-foreground tabular-nums">{elapsed} s</span>
      </div>
      {trail.steps.length > 0 && (
        <ol className="border-border ml-2 flex flex-col gap-1.5 border-l pl-4">
          {trail.steps.map((step) => (
            <StepLine key={step.id} step={step} compact />
          ))}
          <li className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="text-info size-3.5 animate-spin" aria-hidden />
            Working…
          </li>
        </ol>
      )}
    </div>
  );
}

/**
 * One step, in a line. Shared by the live progress, which keeps the plan to its step count, and
 * the evidence panel, which lists the plan.
 */
export function StepLine({ step, compact = false }: { step: TrailStep; compact?: boolean }) {
  let ok = true;
  let text: string;
  if (step.kind === "plan") {
    const count = step.steps.length;
    text = count ? `Planned ${count} step${count === 1 ? "" : "s"}` : "Planned the steps";
    if (!compact && count) {
      return (
        <li className="flex items-start gap-2 text-sm">
          <Check className="text-success mt-0.5 size-3.5 shrink-0" aria-label="Done" />
          <div className="min-w-0">
            <span className="text-muted-foreground">{text}</span>
            <ol className="mt-1.5 flex list-decimal flex-col gap-1 pl-5 text-[13px] leading-relaxed">
              {step.steps.map((line, i) => (
                <li key={i} className="pl-1 break-words">
                  {line.replace(/^\d+\.\s*/, "")}
                </li>
              ))}
            </ol>
          </div>
        </li>
      );
    }
  } else if (step.kind === "verify") {
    ok = step.passed;
    text = step.passed
      ? "Checked the answer against the data"
      : "The checks found problems, so the agent kept working";
  } else {
    ok = step.ok;
    text = toolLabel(step.tool);
    if (step.rowCount !== null) {
      text += ` · ${step.rowCount.toLocaleString()} row${step.rowCount === 1 ? "" : "s"}`;
    }
  }
  return (
    <li className="flex items-start gap-2 text-sm">
      {ok ? (
        <Check className="text-success mt-0.5 size-3.5 shrink-0" aria-label="Done" />
      ) : (
        <X className="text-warning mt-0.5 size-3.5 shrink-0" aria-label="Problem" />
      )}
      <span className={ok ? "text-muted-foreground" : "text-foreground"}>{text}</span>
    </li>
  );
}

function answerText(run: Run): string | null {
  const answer = run.result?.answer;
  if (!answer) return null;
  const formatted = formatAnswer(answer);
  if ("text" in formatted) return formatted.text;
  if ("list" in formatted) return formatted.list.join("\n");
  return [formatted.table.columns, ...formatted.table.rows].map((row) => row.join("\t")).join("\n");
}

function Answer({
  run,
  trail,
  active,
  onShowWork,
  onCorrect,
}: {
  run: Run;
  trail: Trail;
  active: boolean;
  onShowWork: (trigger: HTMLElement) => void;
  onCorrect?: (trigger: HTMLElement) => void;
}) {
  const result = run.result;
  const summary = answerSummary(run.status, result);
  const charts = (result?.steps ?? []).flatMap((step) => (step.chart ? [step.chart] : []));
  const text = answerText(run);
  const [copied, setCopied] = useState(false);
  const question = result?.question_for_user ?? trail.question;

  async function copy() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked: the answer is still on screen to select.
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge tone={summary.tone}>
          {summary.tone === "success" && <Check className="size-3" aria-hidden />}
          {summary.label}
        </StatusBadge>
        {summary.detail && <span className="text-muted-foreground">{summary.detail}</span>}
      </div>

      {run.status === "failed" ? (
        <Callout icon={AlertCircle} tone="danger">
          The run failed{run.error ? `: ${run.error}` : "."}
        </Callout>
      ) : question ? (
        <Callout icon={CircleHelp} tone="warning">
          <span className="font-medium">The agent needs more detail:</span> {question}
          <span className="text-muted-foreground mt-1 block">
            Ask again with that detail in your question.
          </span>
        </Callout>
      ) : result?.answer ? (
        <AnswerValue run={run} />
      ) : (
        <p className="text-muted-foreground text-[15px]">
          {stopReasonText(result?.stop_reason) ?? result?.error ?? "No answer."}
        </p>
      )}

      {charts.length > 0 && (
        <Suspense fallback={<div className="bg-muted h-48 animate-pulse rounded-lg" />}>
          <div className="flex flex-col gap-3 rounded-xl border p-4">
            {charts.map((spec, index) => (
              <Chart key={index} spec={spec} />
            ))}
          </div>
        </Suspense>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={active ? "secondary" : "outline"}
          size="sm"
          aria-pressed={active}
          onClick={(event) => onShowWork(event.currentTarget)}
        >
          <PanelRight />
          Show work
        </Button>
        {text && (
          <Button variant="ghost" size="sm" onClick={copy}>
            {copied ? <Check /> : <Copy />}
            {copied ? "Copied" : "Copy"}
          </Button>
        )}
        {onCorrect && run.status === "succeeded" && (
          <Button
            variant="ghost"
            size="sm"
            title="Teach the agent the rule it got wrong; new questions will follow it"
            onClick={(event) => onCorrect(event.currentTarget)}
          >
            <BookMarked />
            Correct
          </Button>
        )}
      </div>
    </div>
  );
}

function AnswerValue({ run }: { run: Run }) {
  const answer = run.result!.answer!;
  const formatted = formatAnswer(answer);
  return (
    <div className="flex flex-col gap-2">
      {"text" in formatted && (
        <p
          className={
            answer.kind === "unanswerable"
              ? "text-muted-foreground text-lg"
              : "text-3xl font-semibold tracking-tight tabular-nums"
          }
        >
          {formatted.text}
        </p>
      )}
      {"list" in formatted && (
        <ol className="flex flex-col gap-1 text-[15px]">
          {formatted.list.map((item, i) => (
            <li key={i} className="flex gap-3">
              <span className="text-muted-foreground w-5 text-right font-mono text-sm tabular-nums">
                {i + 1}
              </span>
              <span className="font-medium">{item}</span>
            </li>
          ))}
        </ol>
      )}
      {"table" in formatted && (
        <DataTable columns={formatted.table.columns} rows={formatted.table.rows} />
      )}
      {answer.reason && (
        <p className="text-muted-foreground text-[15px] leading-relaxed">{answer.reason}</p>
      )}
    </div>
  );
}

function Callout({
  icon: Icon,
  tone,
  children,
}: {
  icon: typeof AlertCircle;
  tone: "danger" | "warning";
  children: React.ReactNode;
}) {
  const tones = {
    danger: "border-danger-line bg-danger-soft",
    warning: "border-warning-line bg-warning-soft",
  };
  return (
    <div className={`flex gap-3 rounded-lg border px-4 py-3 text-[15px] ${tones[tone]}`}>
      <Icon className={tone === "danger" ? "text-danger mt-0.5 size-4 shrink-0" : "text-warning mt-0.5 size-4 shrink-0"} />
      <div>{children}</div>
    </div>
  );
}
