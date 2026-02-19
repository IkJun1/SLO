from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import APIError
from app.schemas import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthSignupRequest,
    AuthSignupResponse,
    AuthStatusResponse,
    ChatDeleteResponse,
    ChatMessageListResponse,
    ChatMessageItemResponse,
    ChatMessageUpdateRequest,
    ChatSessionCreateRequest,
    ChatSessionListResponse,
    ChatSessionSummaryResponse,
    ChatSessionUpdateRequest,
    DeleteReasonRequest,
    DocApplyPatchRequest,
    DocApplyPatchResponse,
    DocCreateRequest,
    DocDeleteResponse,
    DocIdLookupResponse,
    DocListResponse,
    DocMoveRequest,
    DocMoveResponse,
    DocMutationResponse,
    DocReadResponse,
    DocUpdateRequest,
    FolderCreateRequest,
    FolderDeleteRequest,
    FolderMoveRequest,
    FolderMoveResponse,
    FolderPathResponse,
    GraphResponse,
    ImageDeleteResponse,
    ImageRenameRequest,
    ImageRenameResponse,
    ImageUploadResponse,
    EmbeddingSyncRunRequest,
    EmbeddingSyncRunResponse,
    EmbeddingSyncStatusResponse,
    RagAnswerRequest,
    RagAnswerResponse,
    SearchResponse,
    SyncResponse,
    TrashActionRequest,
    TrashActionResponse,
    TrashListResponse,
)
from app.services import (
    AUTH_COOKIE_MAX_AGE_SECONDS,
    AUTH_COOKIE_NAME,
    answer_with_rag,
    apply_doc_patch,
    create_chat_session,
    create_doc,
    create_folder,
    delete_chat_message,
    delete_chat_session,
    delete_doc,
    delete_image_from_vault,
    delete_folder,
    get_chat_session_messages,
    get_doc,
    get_auth_status,
    get_embedding_sync_status,
    get_image_path,
    get_graph,
    get_sync_changes,
    get_tree_text,
    list_docs,
    list_chat_sessions,
    lookup_doc_ids_by_path,
    move_doc,
    move_folder,
    rename_image_in_vault,
    purge_trash_entry,
    restore_trash_entry,
    run_embedding_sync,
    search_chunks,
    issue_auth_token,
    signup_user,
    login_user,
    upload_image_to_vault,
    update_chat_message,
    update_chat_session,
    update_doc,
    list_trash_items,
)


router = APIRouter()
settings = get_settings()


@router.get("/auth/status", response_model=AuthStatusResponse)
def auth_status_endpoint(db: Session = Depends(get_db)) -> AuthStatusResponse:
    return AuthStatusResponse.model_validate(get_auth_status(db))


@router.post("/auth/signup", response_model=AuthSignupResponse, status_code=201)
def auth_signup_endpoint(payload: AuthSignupRequest, db: Session = Depends(get_db)) -> AuthSignupResponse:
    return AuthSignupResponse.model_validate(signup_user(db, payload.model_dump()))


@router.post("/auth/login", response_model=AuthLoginResponse)
def auth_login_endpoint(
    payload: AuthLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthLoginResponse:
    result = login_user(db, payload.model_dump())
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=issue_auth_token(str(result["username"])),
        httponly=True,
        samesite="lax",
        secure=True,
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
    )
    return AuthLoginResponse.model_validate(result)


@router.get("/tree", response_class=PlainTextResponse)
def tree(
    depth: int | None = Query(default=None, ge=0),
    path_prefix: str = Query(default=""),
) -> PlainTextResponse:
    return PlainTextResponse(get_tree_text(settings, depth, path_prefix))


@router.post("/images", response_model=ImageUploadResponse, status_code=201)
async def upload_image_endpoint(file: UploadFile = File(...)) -> ImageUploadResponse:
    filename = str(file.filename or "").strip()
    if filename == "":
        raise APIError("INVALID_INPUT", 400, "filename is required")

    content_type = str(file.content_type or "").strip().lower()
    data = await file.read()
    saved = upload_image_to_vault(
        settings,
        filename=filename,
        content_type=content_type,
        data=data,
    )
    return ImageUploadResponse.model_validate(saved)


@router.get("/images/by-path")
def get_image_by_path_endpoint(path: str = Query(min_length=1)) -> FileResponse:
    image = get_image_path(settings, path)
    return FileResponse(
        path=image["abs_path"],
        media_type=image["media_type"],
        filename=Path(image["path"]).name,
    )


@router.delete("/images/by-path", response_model=ImageDeleteResponse)
def delete_image_by_path_endpoint(path: str = Query(min_length=1)) -> ImageDeleteResponse:
    result = delete_image_from_vault(settings, path)
    return ImageDeleteResponse.model_validate(result)


@router.post("/images/rename", response_model=ImageRenameResponse)
def rename_image_endpoint(payload: ImageRenameRequest) -> ImageRenameResponse:
    result = rename_image_in_vault(settings, payload.model_dump())
    return ImageRenameResponse.model_validate(result)


@router.get("/docs/by-path", response_model=DocReadResponse)
def read_doc(
    path: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> DocReadResponse:
    return DocReadResponse.model_validate(get_doc(db, settings, path))


@router.get("/docs", response_model=DocListResponse)
def list_docs_endpoint(
    path_prefix: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DocListResponse:
    return DocListResponse.model_validate(list_docs(db, path_prefix))


@router.get("/docs/resolve_ids", response_model=DocIdLookupResponse)
def resolve_doc_ids_endpoint(
    path: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> DocIdLookupResponse:
    return DocIdLookupResponse.model_validate(lookup_doc_ids_by_path(db, settings, path))


@router.post("/docs", response_model=DocMutationResponse, status_code=201)
def create_doc_endpoint(payload: DocCreateRequest, db: Session = Depends(get_db)) -> DocMutationResponse:
    created = create_doc(db, settings, payload.model_dump())
    return DocMutationResponse.model_validate(created)


@router.put("/docs/by-path", response_model=DocMutationResponse)
def update_doc_endpoint(
    payload: DocUpdateRequest,
    path: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> DocMutationResponse:
    updated = update_doc(db, settings, path, payload.model_dump())
    return DocMutationResponse.model_validate(updated)


@router.post("/docs/{doc_id}/apply_patch", response_model=DocApplyPatchResponse)
def apply_patch_doc_endpoint(
    doc_id: UUID,
    payload: DocApplyPatchRequest,
    db: Session = Depends(get_db),
) -> DocApplyPatchResponse:
    patched = apply_doc_patch(db, settings, doc_id, payload.model_dump())
    return DocApplyPatchResponse.model_validate(patched)


@router.delete("/docs/by-path", response_model=DocDeleteResponse)
def delete_doc_endpoint(
    path: str = Query(min_length=1),
    payload: DeleteReasonRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> DocDeleteResponse:
    deleted = delete_doc(db, settings, path, payload.reason if payload else None)
    return DocDeleteResponse.model_validate(deleted)


@router.post("/docs/move", response_model=DocMoveResponse)
def move_doc_endpoint(payload: DocMoveRequest, db: Session = Depends(get_db)) -> DocMoveResponse:
    moved = move_doc(db, settings, payload.model_dump())
    return DocMoveResponse.model_validate(moved)


@router.post("/folders", response_model=FolderPathResponse, status_code=201)
def create_folder_endpoint(payload: FolderCreateRequest, db: Session = Depends(get_db)) -> FolderPathResponse:
    created = create_folder(db, settings, payload.model_dump())
    return FolderPathResponse.model_validate(created)


@router.delete("/folders", response_model=FolderPathResponse)
def delete_folder_endpoint(payload: FolderDeleteRequest, db: Session = Depends(get_db)) -> FolderPathResponse:
    deleted = delete_folder(db, settings, payload.model_dump())
    return FolderPathResponse.model_validate(deleted)


@router.post("/folders/move", response_model=FolderMoveResponse)
def move_folder_endpoint(payload: FolderMoveRequest, db: Session = Depends(get_db)) -> FolderMoveResponse:
    moved = move_folder(db, settings, payload.model_dump())
    return FolderMoveResponse.model_validate(moved)


@router.get("/search", response_model=SearchResponse)
def search_endpoint(
    q: str = Query(min_length=1),
    mode: str = Query(default="keyword"),
    top_k: int = Query(default=20, ge=1, le=100),
    chunk_size: int = Query(default=800, ge=100, le=8000),
    chunk_overlap: int = Query(default=120, ge=0, le=4000),
    path_prefix: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> SearchResponse:
    result = search_chunks(
        db,
        settings,
        query=q,
        mode=mode,
        top_k=top_k,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        path_prefix=path_prefix,
    )
    return SearchResponse.model_validate(result)


@router.get("/graph3d", response_model=GraphResponse)
def graph_endpoint(
    layout: str = Query(default="pca"),
    include_edges: bool = Query(default=False),
    top_k_edges: int = Query(default=3, ge=0),
    path_prefix: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> GraphResponse:
    result = get_graph(
        db,
        layout=layout,
        include_edges=include_edges,
        top_k_edges=top_k_edges,
        path_prefix=path_prefix,
    )
    return GraphResponse.model_validate(result)


@router.get("/sync/changes", response_model=SyncResponse)
def sync_changes_endpoint(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    include_deleted: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> SyncResponse:
    result = get_sync_changes(
        db,
        cursor=cursor,
        limit=limit,
        include_deleted=include_deleted,
    )
    return SyncResponse.model_validate(result)


@router.get("/embeddings/status", response_model=EmbeddingSyncStatusResponse)
def embedding_sync_status_endpoint(db: Session = Depends(get_db)) -> EmbeddingSyncStatusResponse:
    return EmbeddingSyncStatusResponse.model_validate(get_embedding_sync_status(db, settings))


@router.post("/embeddings/run", response_model=EmbeddingSyncRunResponse)
def embedding_sync_run_endpoint(
    payload: EmbeddingSyncRunRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> EmbeddingSyncRunResponse:
    request_payload = payload or EmbeddingSyncRunRequest()
    result = run_embedding_sync(
        db,
        settings,
        limit=request_payload.limit,
        reason="api",
    )
    return EmbeddingSyncRunResponse.model_validate(result)


@router.get("/chat/sessions", response_model=ChatSessionListResponse)
def list_chat_sessions_endpoint(db: Session = Depends(get_db)) -> ChatSessionListResponse:
    return ChatSessionListResponse.model_validate(list_chat_sessions(db))


@router.post("/chat/sessions", response_model=ChatSessionSummaryResponse, status_code=201)
def create_chat_session_endpoint(
    payload: ChatSessionCreateRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> ChatSessionSummaryResponse:
    request_payload = payload or ChatSessionCreateRequest()
    return ChatSessionSummaryResponse.model_validate(create_chat_session(db, request_payload.model_dump()))


@router.patch("/chat/sessions/{session_id}", response_model=ChatSessionSummaryResponse)
def update_chat_session_endpoint(
    session_id: str,
    payload: ChatSessionUpdateRequest,
    db: Session = Depends(get_db),
) -> ChatSessionSummaryResponse:
    return ChatSessionSummaryResponse.model_validate(update_chat_session(db, session_id, payload.model_dump()))


@router.delete("/chat/sessions/{session_id}", response_model=ChatDeleteResponse)
def delete_chat_session_endpoint(session_id: str, db: Session = Depends(get_db)) -> ChatDeleteResponse:
    return ChatDeleteResponse.model_validate(delete_chat_session(db, session_id))


@router.get("/chat/sessions/{session_id}/messages", response_model=ChatMessageListResponse)
def get_chat_session_messages_endpoint(session_id: str, db: Session = Depends(get_db)) -> ChatMessageListResponse:
    return ChatMessageListResponse.model_validate(get_chat_session_messages(db, session_id))


@router.patch("/chat/messages/{message_id}", response_model=ChatMessageItemResponse)
def update_chat_message_endpoint(
    message_id: str,
    payload: ChatMessageUpdateRequest,
    db: Session = Depends(get_db),
) -> ChatMessageItemResponse:
    return ChatMessageItemResponse.model_validate(update_chat_message(db, settings, message_id, payload.model_dump()))


@router.delete("/chat/messages/{message_id}", response_model=ChatDeleteResponse)
def delete_chat_message_endpoint(message_id: str, db: Session = Depends(get_db)) -> ChatDeleteResponse:
    return ChatDeleteResponse.model_validate(delete_chat_message(db, message_id))


@router.post("/llm/answer", response_model=RagAnswerResponse)
def llm_answer_endpoint(payload: RagAnswerRequest, db: Session = Depends(get_db)) -> RagAnswerResponse:
    result = answer_with_rag(db, settings, payload.model_dump())
    return RagAnswerResponse.model_validate(result)


@router.get("/trash", response_model=TrashListResponse)
def list_trash_endpoint(db: Session = Depends(get_db)) -> TrashListResponse:
    return TrashListResponse.model_validate(list_trash_items(db, settings))


@router.post("/trash/restore", response_model=TrashActionResponse)
def restore_trash_endpoint(payload: TrashActionRequest, db: Session = Depends(get_db)) -> TrashActionResponse:
    result = restore_trash_entry(db, settings, payload.model_dump())
    return TrashActionResponse.model_validate(result)


@router.delete("/trash", response_model=TrashActionResponse)
def purge_trash_endpoint(payload: TrashActionRequest, db: Session = Depends(get_db)) -> TrashActionResponse:
    result = purge_trash_entry(db, settings, payload.model_dump())
    return TrashActionResponse.model_validate(result)
