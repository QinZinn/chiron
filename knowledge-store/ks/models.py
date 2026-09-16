"""Kiểu dữ liệu của KS. Tất cả frozen — không mutate sau khi dựng."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class SourceModule(str, Enum):
    MNEMOSYNE = "mnemosyne"
    LEXIFLASH = "lexiflash"


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


# Quan hệ đối xứng: lưu một chiều, query hai chiều.
SYMMETRIC_RELATIONS = frozenset({RelationType.RELATED, RelationType.CONTRASTS_WITH})


@dataclass(frozen=True)
class ConceptDraft:
    """Một khái niệm chờ ghi. 4 field, tất cả bắt buộc, không optional."""

    title: str
    subject: str
    summary: str
    source_module: SourceModule


@dataclass(frozen=True)
class DuplicateCandidate:
    """Node có sẵn giống draft. score là similarity trigram trên title."""

    node_id: UUID
    title: str
    score: float


@dataclass(frozen=True)
class IngestedConcept:
    """Kết quả ghi một draft.

    candidates populate CẢ KHI created=True — near-miss dưới ngưỡng cũng phải
    log, nếu chỉ log ca merge thì dữ liệu một chiều, không đo được false negative.
    """

    draft: ConceptDraft
    node_id: UUID
    created: bool  # True = tạo mới, False = khớp node có sẵn
    candidates: tuple[DuplicateCandidate, ...]


@dataclass(frozen=True)
class IngestResult:
    ingested: tuple[IngestedConcept, ...]


@dataclass(frozen=True)
class NodeSummary:
    """Cho GET /nodes — KHÔNG có edges."""

    id: UUID
    title: str
    subject: str
    summary: str


@dataclass(frozen=True)
class SaveResult:
    """Kết quả save_transcript. ok=False kèm error thay vì raise."""

    ok: bool
    transcript_id: UUID | None
    error: str | None


@dataclass(frozen=True)
class NeighborCandidate:
    """Node đưa vào prompt gợi ý edge. score là similarity trigram."""

    node_id: UUID
    title: str
    subject: str
    summary: str
    score: float


@dataclass(frozen=True)
class EdgeSuggestion:
    """Một cạnh LLM đề xuất, đã ghi vào ks.edges với status='pending'."""

    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    to_title: str
    relation_type: RelationType
    reason: str


@dataclass(frozen=True)
class SuggestionRun:
    """Kết quả một lần suggest_edges. KHÔNG raise khi LLM lỗi — outcome ghi lại."""

    node_id: UUID
    candidates: tuple[NeighborCandidate, ...]
    suggestions: tuple[EdgeSuggestion, ...]
    outcome: str  # 'ok' | 'no_candidates' | 'llm_error' | 'parse_error'
    error: str | None = None


@dataclass(frozen=True)
class PendingEdge:
    """Cạnh chờ duyệt, kèm tên hai đầu để người đọc quyết được."""

    edge_id: UUID
    from_node_id: UUID
    from_title: str
    to_node_id: UUID
    to_title: str
    relation_type: RelationType
    suggested_by: SuggestedBy


@dataclass(frozen=True)
class Neighbor:
    """Node kề qua một cạnh đã approved. direction: 'out' | 'in' | 'both'."""

    node_id: UUID
    title: str
    relation_type: RelationType
    direction: str


@dataclass(frozen=True)
class ExtractedConcept:
    """Khái niệm LLM rút ra từ transcript, CHỜ người xác nhận."""

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
    """Kết quả một lần chạy extract. KHÔNG raise khi LLM lỗi — outcome ghi lại."""

    transcript_id: UUID
    concepts: tuple[ExtractedConcept, ...]
    ok: bool
    attempts: int
    error: str | None = None
