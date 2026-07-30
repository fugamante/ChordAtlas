"""Private Stage 2 analysis domain; intentionally separate from SongChart."""

from chordatlas.analysis.baseline import MAX_ANALYSIS_SECONDS, analyze_pcm16_wav
from chordatlas.analysis.evaluation import ReferenceSegment, evaluate_timeline
from chordatlas.analysis.models import (
    AnalysisConfig,
    AnalysisError,
    AnalysisRun,
    AnalysisSpec,
    ChordCandidate,
    ChordCandidateTimeline,
    ChordSegment,
    EngineRef,
    KeyHypothesis,
    RunState,
    TempoHypothesis,
)
from chordatlas.analysis.service import AnalysisService
from chordatlas.analysis.store import AnalysisStore

__all__ = [
    "MAX_ANALYSIS_SECONDS",
    "AnalysisConfig",
    "AnalysisError",
    "AnalysisRun",
    "AnalysisService",
    "AnalysisSpec",
    "AnalysisStore",
    "ChordCandidate",
    "ChordCandidateTimeline",
    "ChordSegment",
    "EngineRef",
    "KeyHypothesis",
    "ReferenceSegment",
    "RunState",
    "TempoHypothesis",
    "analyze_pcm16_wav",
    "evaluate_timeline",
]
