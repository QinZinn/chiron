"""KS data types. All frozen — never mutated after construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class SourceModule(str, Enum):
    MNEMOSYNE = "mnemosyne"
    LEXIFLASH = "lexiflash"
    NOTE_SCAN = "note_scan"  # notes scanned through OCR (ks/notes.py)


class RelationType(str, Enum):
    PREREQUISITE = "prerequisite"
    RELATED = "related"
    CONTRASTS_WITH = "contrasts_with"


class SuggestedBy(str, Enum):
    LLM = "llm"
    MANUAL = "manual"


class EdgeStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# Symmetric relations: stored in one direction, queried in both.
SYMMETRIC_RELATIONS = frozenset({RelationType.RELATED, RelationType.CONTRASTS_WITH})


@dataclass(frozen=True)
class ConceptDraft:
    """A concept waiting to be written. 4 fields, all required, none optional."""

    title: str
    subject: str
    summary: str
    source_module: SourceModule


@dataclass(frozen=True)
class DuplicateCandidate:
    """An existing node similar to a draft. score is trigram similarity on the title."""

    node_id: UUID
    title: str
    score: float


@dataclass(frozen=True)
class IngestedConcept:
    """The result of writing one draft.

    candidates is populated EVEN WHEN created=True — near-misses below the
    threshold must be logged too; logging only merges gives one-sided data and
    no way to measure false negatives.
    """

    draft: ConceptDraft
    node_id: UUID
    created: bool  # True = newly created, False = matched an existing node
    candidates: tuple[DuplicateCandidate, ...]


@dataclass(frozen=True)
class IngestResult:
    ingested: tuple[IngestedConcept, ...]


@dataclass(frozen=True)
class NodeSummary:
    """For GET /nodes — WITHOUT edges."""

    id: UUID
    title: str
    subject: str
    summary: str


@dataclass(frozen=True)
class EdgeSummary:
    """For GET /edges: one APPROVED edge, both ends already resolved through merges."""

    id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str


@dataclass(frozen=True)
class SaveResult:
    """Result of save_transcript. ok=False carries error instead of raising."""

    ok: bool
    transcript_id: UUID | None
    error: str | None


@dataclass(frozen=True)
class NeighborCandidate:
    """A node included in the edge-suggestion prompt. score is trigram similarity."""

    node_id: UUID
    title: str
    subject: str
    summary: str
    score: float


@dataclass(frozen=True)
class EdgeSuggestion:
    """One edge the LLM suggested, written to ks.edges with status='pending'."""

    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    to_title: str
    relation_type: RelationType
    reason: str


@dataclass(frozen=True)
class SuggestionRun:
    """Result of one suggest_edges run. Does NOT raise on LLM errors — the outcome records it."""

    node_id: UUID
    candidates: tuple[NeighborCandidate, ...]
    suggestions: tuple[EdgeSuggestion, ...]
    outcome: str  # 'ok' | 'no_candidates' | 'llm_error' | 'parse_error'
    error: str | None = None


@dataclass(frozen=True)
class PendingEdge:
    """An edge awaiting review, with both ends' titles so a reader can decide."""

    edge_id: UUID
    from_node_id: UUID
    from_title: str
    to_node_id: UUID
    to_title: str
    relation_type: RelationType
    suggested_by: SuggestedBy


@dataclass(frozen=True)
class Neighbor:
    """A node adjacent through an approved edge. direction: 'out' | 'in' | 'both'."""

    node_id: UUID
    title: str
    relation_type: RelationType
    direction: str


@dataclass(frozen=True)
class ExtractedConcept:
    """A concept the LLM extracted from a transcript, AWAITING human confirmation."""

    id: UUID
    transcript_id: UUID
    title: str
    subject: str
    summary: str
    source_module: SourceModule
    status: str  # 'pending_review' | 'accepted' | 'discarded'
    node_id: UUID | None


@dataclass(frozen=True)
class ExtractionResult:
    """Result of one extract run. Does NOT raise on LLM errors — the outcome records it."""

    transcript_id: UUID
    concepts: tuple[ExtractedConcept, ...]
    ok: bool
    attempts: int
    error: str | None = None
