import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { type Api, type Run } from "@/api";
import { errorMessage } from "@/i18n/errors";
import { useT } from "@/i18n/context";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  feedbackReasons,
  type FeedbackReason,
  type Rating,
  type RunFeedback,
  nextFeedback,
} from "@/feedback";

const MAX_NOTE = 1000;

/**
 * Thumbs on a finished answer (docs/adr/0043). A thumbs-up says the answer is right; a thumbs-down
 * opens optional reasons and a note. Clicking the chosen thumb again takes the rating back.
 */
export function RunFeedbackControl({ api, run }: { api: Api; run: Run }) {
  const m = useT();
  const [feedback, setFeedback] = useState<RunFeedback | null>(run.feedback ?? null);
  const [asking, setAsking] = useState(false);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thanks, setThanks] = useState(false);

  async function save(change: () => Promise<RunFeedback | null>) {
    setBusy(true);
    setError(null);
    try {
      setFeedback(await change());
      return true;
    } catch (caught) {
      setError(errorMessage(caught, m));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function rate(clicked: Rating) {
    const next = nextFeedback(feedback?.rating ?? null, clicked);
    setThanks(false);
    if (next === null) {
      setAsking(false);
      await save(async () => {
        await api.clearFeedback(run.id);
        return null;
      });
      return;
    }
    // The rating counts at once; reasons for a thumbs-down are optional extras.
    const saved = await save(() => api.setFeedback(run.id, { rating: next }));
    if (saved && next === "down") {
      setReasons([]);
      setNote("");
      setAsking(true);
    } else {
      setAsking(false);
      if (saved) setThanks(true);
    }
  }

  async function send() {
    const text = note.trim();
    const saved = await save(() =>
      api.setFeedback(run.id, { rating: "down", reasons, ...(text ? { note: text } : {}) }),
    );
    if (saved) {
      setAsking(false);
      setThanks(true);
    }
  }

  function toggle(reason: FeedbackReason) {
    setReasons((current) =>
      current.includes(reason) ? current.filter((r) => r !== reason) : [...current, reason],
    );
  }

  const rating = feedback?.rating ?? null;
  return (
    <>
      <div className="ml-auto flex items-center gap-1">
        <span className="text-muted-foreground text-xs" aria-live="polite">
          {thanks ? m.feedback.thanks : ""}
        </span>
        {rating === "down" && !asking && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 text-xs"
            disabled={busy}
            onClick={() => {
              setReasons(feedback?.reasons ?? []);
              setNote(feedback?.note ?? "");
              setThanks(false);
              setAsking(true);
            }}
          >
            {m.feedback.sayWhy}
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label={m.feedback.good}
          aria-pressed={rating === "up"}
          title={rating === "up" ? m.feedback.takeBack : m.feedback.goodHint}
          disabled={busy}
          onClick={() => rate("up")}
        >
          <ThumbsUp className={rating === "up" ? "fill-current" : undefined} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          aria-label={m.feedback.bad}
          aria-pressed={rating === "down"}
          title={rating === "down" ? m.feedback.takeBack : m.feedback.bad}
          disabled={busy}
          onClick={() => rate("down")}
        >
          <ThumbsDown className={rating === "down" ? "fill-current" : undefined} />
        </Button>
      </div>
      {asking && (
        <div className="bg-card flex basis-full flex-col gap-3 rounded-xl border p-3">
          <p className="text-sm font-medium">{m.feedback.whatWrong}</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label={m.feedback.reasonsLabel}>
            {feedbackReasons(m).map((reason) => (
              <Button
                key={reason.value}
                type="button"
                variant={reasons.includes(reason.value) ? "secondary" : "outline"}
                size="sm"
                disabled={busy}
                aria-pressed={reasons.includes(reason.value)}
                onClick={() => toggle(reason.value)}
              >
                {reason.label}
              </Button>
            ))}
          </div>
          <Textarea
            aria-label={m.feedback.note}
            placeholder={m.feedback.notePlaceholder}
            maxLength={MAX_NOTE}
            rows={2}
            disabled={busy}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => setAsking(false)}
            >
              {m.feedback.skip}
            </Button>
            <Button type="button" size="sm" disabled={busy} onClick={send}>
              {m.feedback.send}
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-danger basis-full text-sm">
          {error}
        </p>
      )}
    </>
  );
}
