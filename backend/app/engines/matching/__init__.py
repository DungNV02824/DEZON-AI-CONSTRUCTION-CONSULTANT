"""Matching Engine (đặc tả mục 7): hàm thuần chấm điểm, xếp hạng và sinh lý do đề xuất nhà thầu."""
from app.engines.matching.criteria import (
    PROFILE_FIELDS,
    CapabilityFact,
    ContractorFacts,
    FieldSource,
    ProjectFact,
    missing_important_fields,
    profile_completeness,
    verified_ratio,
)
from app.engines.matching.reasons import EVIDENCE_KEYS, build_cautions, build_evidence, build_reasons
from app.engines.matching.scorer import (
    CRITERIA_KEYS,
    CRITERIA_LABEL_VI,
    FLAG_VERIFY_PRICE,
    RankingResult,
    ScoredContractor,
    estimate_cost,
    rank_contractors,
    score_contractor,
)

__all__ = [
    "CRITERIA_KEYS", "CRITERIA_LABEL_VI", "EVIDENCE_KEYS", "FLAG_VERIFY_PRICE", "PROFILE_FIELDS",
    "CapabilityFact", "ContractorFacts", "FieldSource", "ProjectFact", "RankingResult", "ScoredContractor",
    "build_cautions", "build_evidence", "build_reasons", "estimate_cost", "missing_important_fields",
    "profile_completeness", "rank_contractors", "score_contractor", "verified_ratio",
]
