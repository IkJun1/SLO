from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DocCreateRequest(BaseModel):
    path: str = Field(min_length=1)
    title: str | None = None
    content: str = ""
    create_parents: bool = False
    overwrite: bool = False


class DocUpdateRequest(BaseModel):
    content: str
    title: str | None = None
    expected_hash: str | None = None
    reason: str | None = None


class DocPatchOperation(BaseModel):
    op: Literal["append", "prepend", "replace", "insert_before", "insert_after"]
    text: str | None = None
    target: str | None = None
    occurrence: Literal["first", "last"] = "first"
    count: int | None = Field(default=None, ge=1)


class DocApplyPatchRequest(BaseModel):
    ops: list[DocPatchOperation] = Field(min_length=1)
    expected_hash: str | None = None
    reason: str | None = None


class DocApplyPatchResponse(BaseModel):
    id: UUID
    path: str
    title: str
    content_hash: str
    applied_ops: int


class DeleteReasonRequest(BaseModel):
    reason: str | None = None


class DocMoveRequest(BaseModel):
    doc_id: UUID
    to_path: str = Field(min_length=1)
    overwrite: bool = False


class FolderCreateRequest(BaseModel):
    path: str = Field(min_length=1)
    create_parents: bool = False


class FolderDeleteRequest(BaseModel):
    path: str = Field(min_length=1)
    recursive: bool = False
    reason: str | None = None


class FolderMoveRequest(BaseModel):
    from_path: str = Field(min_length=1)
    to_path: str = Field(min_length=1)
    overwrite: bool = False


class DocReadResponse(BaseModel):
    id: UUID
    path: str
    title: str
    content: str


class DocListItemResponse(BaseModel):
    id: UUID
    path: str
    title: str
    updated_at: datetime


class DocListResponse(BaseModel):
    docs: list[DocListItemResponse]


class DocIdLookupResponse(BaseModel):
    doc_ids: list[UUID]


class DocMutationResponse(BaseModel):
    id: UUID
    path: str
    title: str


class ImageUploadResponse(BaseModel):
    path: str
    url: str
    markdown: str
    content_type: str
    size: int = Field(ge=1)


class ImageDeleteResponse(BaseModel):
    path: str
    deleted: bool


class ImageRenameRequest(BaseModel):
    from_path: str = Field(min_length=1)
    to_path: str = Field(min_length=1)
    overwrite: bool = False


class ImageRenameResponse(BaseModel):
    from_path: str
    to_path: str


class DocDeleteResponse(BaseModel):
    id: UUID
    path: str


class DocMoveResponse(BaseModel):
    id: UUID
    from_path: str
    to_path: str


class FolderPathResponse(BaseModel):
    path: str


class FolderMoveResponse(BaseModel):
    from_path: str
    to_path: str


class TrashActionRequest(BaseModel):
    entry_type: Literal["doc", "folder"]
    doc_id: UUID | None = None
    trash_path: str | None = None


class TrashEntryResponse(BaseModel):
    entry_type: Literal["doc", "folder"]
    doc_id: UUID | None = None
    trash_path: str
    original_path: str
    title: str | None = None
    deleted_at: datetime


class TrashListResponse(BaseModel):
    items: list[TrashEntryResponse]


class TrashActionResponse(BaseModel):
    entry_type: Literal["doc", "folder"]
    restored_path: str | None = None
    deleted: bool = False


class SearchHitResponse(BaseModel):
    doc_id: UUID
    doc_path: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    chunk_start: int = Field(ge=0)
    chunk_end: int = Field(ge=0)
    snippet: str


class SearchResponse(BaseModel):
    hits: list[SearchHitResponse]


class AuthStatusResponse(BaseModel):
    has_users: bool


class AuthSignupRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class AuthSignupResponse(BaseModel):
    username: str


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class AuthLoginResponse(BaseModel):
    authenticated: bool
    username: str


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=256)


class ChatSessionUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)


class ChatSessionSummaryResponse(BaseModel):
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionSummaryResponse]


class ChatMessageItemResponse(BaseModel):
    message_id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    source_doc_paths: list[str] = Field(default_factory=list)
    created_at: datetime


class ChatMessageUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class ChatMessageListResponse(BaseModel):
    session: ChatSessionSummaryResponse
    messages: list[ChatMessageItemResponse]


class ChatDeleteResponse(BaseModel):
    deleted: bool


class RagHistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class RagAnswerRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    top_k: int = Field(default=6, ge=1, le=20)
    mode: Literal["vector"] = "vector"
    chunk_size: int = Field(default=800, ge=100, le=8000)
    chunk_overlap: int = Field(default=120, ge=0, le=4000)
    history: list[RagHistoryTurn] = Field(default_factory=list, max_length=20)
    api_base: str | None = None
    model: str | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)


class RagSourceResponse(BaseModel):
    doc_id: UUID
    doc_path: str
    chunk_id: str
    chunk_index: int = Field(ge=0)
    chunk_start: int = Field(ge=0)
    chunk_end: int = Field(ge=0)
    snippet: str


class RagAnswerResponse(BaseModel):
    session_id: str
    session_title: str
    answer: str
    sources: list[RagSourceResponse]
    model: str
    degraded: bool


class GraphNodeResponse(BaseModel):
    node_id: str
    node_type: Literal["doc", "folder"]
    doc_id: UUID | None = None
    path: str
    title: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    layout: str
    updated_at: datetime


class GraphEdgeResponse(BaseModel):
    from_node_id: str
    to_node_id: str
    edge_type: Literal["folder_tree", "similarity"]
    weight: float


class GraphDataResponse(BaseModel):
    layout: str
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class MetaResponse(BaseModel):
    request_id: str
    timestamp: str


class GraphResponse(BaseModel):
    data: GraphDataResponse
    meta: MetaResponse


class SyncChangeResponse(BaseModel):
    seq: int
    resource: Literal["doc", "folder", "chunk"]
    action: Literal["created", "updated", "moved", "deleted"]
    id: UUID | None
    path: str
    occurred_at: datetime
    payload: dict[str, Any]


class SyncDataResponse(BaseModel):
    changes: list[SyncChangeResponse]
    next_cursor: str
    has_more: bool


class SyncResponse(BaseModel):
    data: SyncDataResponse
    meta: MetaResponse


class EmbeddingSyncRunRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=10000)


class EmbeddingSyncRunResponse(BaseModel):
    started: bool
    running: bool
    provider: str
    refreshed_docs: int = Field(ge=0)
    created_embd: int = Field(ge=0)
    updated_embd: int = Field(ge=0)
    deleted_embd: int = Field(ge=0)
    pending_before: int = Field(ge=0)
    processed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    remaining_pending: int = Field(ge=0)
    message: str | None = None


class EmbeddingSyncStatusResponse(BaseModel):
    running: bool
    provider: str
    pending: int = Field(ge=0)
    ready: int = Field(ge=0)
    failed: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    total: int = Field(ge=0)
    last_run_at: datetime | None = None
