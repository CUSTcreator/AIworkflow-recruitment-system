export interface ScoreSnapshotView {
  total?: number;
  capabilityTotal?: number;
  jobFit?: number;
  resumeExperience?: number;
  education?: number | null;
  stage?: string;
  scoreExplanation?: Record<string, unknown>;
}

function numberFrom(source: Record<string, unknown>, keys: readonly string[]): number | undefined {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
}

export function readScoreSnapshot(value: unknown): ScoreSnapshotView | undefined {
  if (!value || typeof value !== "object") return undefined;
  const source = value as Record<string, unknown>;
  const view: ScoreSnapshotView = {
    total: numberFrom(source, ["candidate_ability_score", "baseScore", "base_score", "overallScore", "overall_score", "score"]),
    capabilityTotal: numberFrom(source, ["capability_total_score", "role_capability_score", "baseScore", "base_score"]),
    jobFit: numberFrom(source, ["jobCapabilityFitScore", "job_capability_fit_score"]),
    resumeExperience: numberFrom(source, ["resumeDemonstratedCapabilityScore", "resume_demonstrated_capability_score", "resumeExperienceScore", "resume_experience_score"]),
    education: numberFrom(source, ["educationBackgroundScore", "education_background_score"]),
    stage: typeof source.stage === "string" ? source.stage : undefined,
    scoreExplanation: (
      source.scoreExplanation && typeof source.scoreExplanation === "object"
        ? source.scoreExplanation
        : source.score_explanation_detail && typeof source.score_explanation_detail === "object"
          ? source.score_explanation_detail
          : undefined
    ) as Record<string, unknown> | undefined
  };
  return Object.values(view).some((item) => item !== undefined) ? view : undefined;
}
