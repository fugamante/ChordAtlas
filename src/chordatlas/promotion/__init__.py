"""Private Stage 4 approval and promotion domain, separate from SongChart."""

from chordatlas.promotion.models import (
    ApprovalRecord,
    GuitarDecision,
    MappingConfig,
    PromotionError,
    PromotionIssue,
    PromotionResult,
    RevocationRecord,
)
from chordatlas.promotion.service import PromotionService

__all__ = [
    "ApprovalRecord",
    "GuitarDecision",
    "MappingConfig",
    "PromotionError",
    "PromotionIssue",
    "PromotionResult",
    "PromotionService",
    "RevocationRecord",
]
