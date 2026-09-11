import type { CoverageStatus, GateStatus } from "./contracts";

export const gateLabels: Record<GateStatus, string> = {
  verified: "资格通过",
  unclear: "资格不清",
  not_qualified: "资格不满足"
};

export const coverageLabels: Record<CoverageStatus, string> = {
  covered: "已覆盖",
  partially_covered: "部分覆盖",
  not_covered: "未覆盖",
  unclear: "不清晰"
};
