"""Authorized direct-media acquisition outside the SongChart contract."""

from chordatlas.acquisition.models import (
    AcquisitionError,
    AcquisitionRequest,
    DirectHttpsSource,
    DownloadResult,
)
from chordatlas.acquisition.service import AcquisitionService
from chordatlas.acquisition.transport import (
    AcquisitionTransport,
    PinnedHttpsTransport,
)

__all__ = [
    "AcquisitionError",
    "AcquisitionRequest",
    "AcquisitionService",
    "AcquisitionTransport",
    "DirectHttpsSource",
    "DownloadResult",
    "PinnedHttpsTransport",
]
