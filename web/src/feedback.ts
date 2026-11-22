// Feedback on answers (docs/adr/0043): a thumbs-up or -down, and on a thumbs-down, why.

import type { Messages } from "./i18n/en";

export type Rating = "up" | "down";

/** The reasons the API accepts, in the order shown. */
const FEEDBACK_REASON_VALUES = [
  "wrong_number",
  "misunderstood",
  "should_abstain",
  "unclear",
  "other",
] as const;

export type FeedbackReason = (typeof FEEDBACK_REASON_VALUES)[number];

export function feedbackReasons(m: Messages): { value: FeedbackReason; label: string }[] {
  return FEEDBACK_REASON_VALUES.map((value) => ({ value, label: m.feedback.reasons[value] }));
}

export interface RunFeedback {
  rating: Rating;
  reasons: FeedbackReason[];
  note: string | null;
}

/** The rating after a click: the clicked thumb, or none when it was already chosen. */
export function nextFeedback(current: Rating | null, clicked: Rating): Rating | null {
  return current === clicked ? null : clicked;
}
