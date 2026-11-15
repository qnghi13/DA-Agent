import { Check, Loader2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { type Api, type MetricFacts, errorMessage } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MAX_NAME, metricSentence, readMetricName } from "@/metrics";

export interface MetricChoice {
  runId: string;
  datasetId: string;
  queryIndex: number;
  candidateIndex: number;
  facts: MetricFacts;
}

/**
 * Names one measure of an answer so later questions compute that word the same way
 * (docs/design/2026-11-15-metric-definitions.md). The server reads the calculation from the
 * run; this dialog only sends which measure and its names.
 */
export function SaveMetricDialog({
  api,
  choice,
  onClose,
}: {
  api: Api;
  choice: MetricChoice | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={choice !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        {choice && <SaveMetricForm key={`${choice.queryIndex}:${choice.candidateIndex}`} api={api} choice={choice} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  );
}

function SaveMetricForm({ api, choice, onClose }: { api: Api; choice: MetricChoice; onClose: () => void }) {
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const read = readMetricName(name, aliases);
    if ("error" in read) {
      setError(read.error);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.saveMetric(choice.datasetId, {
        run_id: choice.runId,
        query_index: choice.queryIndex,
        candidate_index: choice.candidateIndex,
        name: read.name,
        aliases: read.aliases,
      });
      setSaved(true);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>Save a definition</DialogTitle>
        <DialogDescription>
          New questions that use this name are calculated this way, for everyone in the workspace.
        </DialogDescription>
      </DialogHeader>
      <p className="bg-muted rounded-lg px-3 py-2 text-sm leading-relaxed">{metricSentence(choice.facts)}</p>
      {saved ? (
        <p role="status" className="flex items-center gap-2 text-sm">
          <Check className="text-success size-4" aria-hidden />
          Saved. Used from your next question.
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="metric-name">Name</Label>
            <Input
              id="metric-name"
              autoFocus
              maxLength={MAX_NAME}
              placeholder="Revenue"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="metric-aliases">Other names (comma-separated)</Label>
            <Input
              id="metric-aliases"
              placeholder="Sales, turnover"
              value={aliases}
              onChange={(event) => setAliases(event.target.value)}
            />
          </div>
          <p className="text-muted-foreground text-xs">
            Saving a name you already use replaces its old definition.
          </p>
        </>
      )}
      {error && (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      )}
      <DialogFooter>
        {saved ? (
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              Save definition
            </Button>
          </>
        )}
      </DialogFooter>
    </form>
  );
}
