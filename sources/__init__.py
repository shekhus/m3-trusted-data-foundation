"""Source adapters: the seam between the pipeline and wherever extracts come from (docs/plan.md A1).

The shipped adapter reads files (generated extracts and uploads). A real Infor M3 adapter (ION API or data
lake) implements the same protocol; field-level mapping to M3 physical columns is to confirm with an M3 SME.
"""

from sources.base import Extract, ExtractRef, SourceAdapter, SourceUnavailable
from sources.filesystem import FileSystemAdapter

__all__ = ["Extract", "ExtractRef", "FileSystemAdapter", "SourceAdapter", "SourceUnavailable"]
