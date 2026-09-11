import type { FirstInterviewProgressDraft, SecondInterviewProgressDraft } from "./contracts";

export interface CompleteFirstInterviewRequest extends FirstInterviewProgressDraft {
  decision: "pass" | "reject";
  effectiveAt: string;
  timezone: string;
}

export interface CompleteSecondInterviewRequest extends SecondInterviewProgressDraft {
  decision: "pass" | "reject";
  effectiveAt: string;
  timezone: string;
}
