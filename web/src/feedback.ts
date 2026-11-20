// Feedback on answers (docs/adr/0043): a thumbs-up or -down, and on a thumbs-down, why.

export type Rating = "up" | "down";

/** The reasons the API accepts, in the order shown. */
export const FEEDBACK_REASONS = [
  { value: "wrong_number", label: "Wrong number" },
  { value: "misunderstood", label: "Answered a different question" },
  { value: "should_abstain", label: "Should have said it can't tell" },
  { value: "unclear", label: "Hard to understand" },
  { value: "other", label: "Something else" },
] as const;

export type FeedbackReason = (typeof FEEDBACK_REASONS)[number]["value"];

export interface RunFeedback {
  rating: Rating;
  reasons: FeedbackReason[];
  note: string | null;
}

/** The rating after a click: the clicked thumb, or none when it was already chosen. */
export function nextFeedback(current: Rating | null, clicked: Rating): Rating | null {
  return current === clicked ? null : clicked;
}
