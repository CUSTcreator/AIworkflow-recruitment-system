import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const baselines = {
  "src/modules/assessment/components/CandidateDecisionOverviewCard.tsx":
    "091b021c24e559f122b42d2c7b20828f25600e62e72665623c3be5dd69acf4ad",
  "src/styles/index.css":
    "5b631ec26cb712cc81d0012cf5d21591c75f18b74eb68babd202242262294c1d"
};

const changed = Object.entries(baselines).filter(([path, expected]) => {
  const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
  return actual !== expected;
});

if (changed.length) {
  console.error(
    "视觉基线检查失败；以下文件包含受保护的展示样式改动：\n- " +
      changed.map(([path]) => path).join("\n- ")
  );
  process.exit(1);
}

console.log("前端视觉基线检查通过");
