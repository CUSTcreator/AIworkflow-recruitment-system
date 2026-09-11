"""V2/V3 共用的纯增量评分算法管线。"""

from .contracts import (
    AffectedRecomputationPlan,
    IncrementalEvidence,
    IncrementalEvidenceInput,
    IncrementalScoringInput,
    IncrementalScoringResult,
)
from .topology_contracts import (
    AnchorJudgement,
    AnchorSelection,
    AnchorUpdate,
    FrozenTopologyInput,
    InterviewAssertion,
    RegionRoute,
    ScoringRegion,
    TopologyScoringInput,
    TopologyScoringResult,
)
from .topology_pipeline import (
    TopologyScoringNotImplemented,
    apply_updates_and_aggregate,
    derive_anchor_updates,
    route_assertions_to_regions,
    select_non_overlapping_anchors,
)



from .topology_pipeline_runner import run_topology_incremental_scoring, topology_result_as_dict
__all__ = [
    "AffectedRecomputationPlan",
    "IncrementalEvidence",
    "IncrementalEvidenceInput",
    "IncrementalScoringInput",
    "IncrementalScoringResult",
    "AnchorJudgement",
    "AnchorSelection",
    "AnchorUpdate",
    "FrozenTopologyInput",
    "InterviewAssertion",
    "RegionRoute",
    "ScoringRegion",
    "TopologyScoringInput",
    "TopologyScoringResult",
    "TopologyScoringNotImplemented",
    "route_assertions_to_regions",
    "select_non_overlapping_anchors",
    "derive_anchor_updates",
    "apply_updates_and_aggregate",
    "run_topology_incremental_scoring",
    "topology_result_as_dict",
]


