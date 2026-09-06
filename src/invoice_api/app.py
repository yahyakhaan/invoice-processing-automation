from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response, StreamingResponse

from invoice_agent.config import Settings, get_settings
from invoice_agent.storage import ensure_database
from invoice_api.schemas import (
    EventList,
    HealthResponse,
    PublicEvent,
    ReviewAccepted,
    ReviewRequest,
    RunCreated,
    RunDetail,
    RunList,
    RunSummary,
    SampleInvoice,
)
from invoice_api.service import TERMINAL_STATUSES, RunService
from invoice_api.sources import (
    SampleCatalog,
    SourceRejected,
    save_upload,
)

STREAM_END_STATUSES = TERMINAL_STATUSES | {"PENDING_REVIEW"}


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    catalog = SampleCatalog()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        ensure_database(resolved_settings)
        application.state.run_service = RunService(resolved_settings)
        application.state.run_tasks = set()
        yield
        tasks = list(application.state.run_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    application = FastAPI(
        title="Acme Invoice Automation API",
        version="0.2.0",
        lifespan=lifespan,
    )

    def service(request: Request) -> RunService:
        return request.app.state.run_service

    def schedule(request: Request, function: Any, *args: Any, **kwargs: Any) -> None:
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        request.app.state.run_tasks.add(task)
        task.add_done_callback(request.app.state.run_tasks.discard)

    def get_record_or_404(run_service: RunService, run_id: UUID) -> dict[str, Any]:
        record = run_service.get(str(run_id))
        if record is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return record

    @application.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            database_backend="postgres" if resolved_settings.database_url else "sqlite",
            provider=resolved_settings.provider,
        )

    @application.get("/api/samples", response_model=list[SampleInvoice], tags=["samples"])
    def samples() -> list[SampleInvoice]:
        return catalog.list()

    @application.get("/api/samples/{sample_id}/content", tags=["samples"])
    def sample_content(sample_id: str) -> FileResponse:
        try:
            source = catalog.resolve(sample_id)
            content_type = catalog.content_type(sample_id)
        except SourceRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        assert source.path is not None
        return FileResponse(source.path, media_type=content_type, filename=source.path.name)

    @application.post(
        "/api/runs",
        response_model=RunCreated,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["runs"],
    )
    async def create_run(
        request: Request,
        file: Annotated[UploadFile | None, File()] = None,
        sample_id: Annotated[str | None, Form()] = None,
    ) -> RunCreated:
        if (file is None) == (sample_id is None):
            raise HTTPException(
                status_code=400,
                detail="Provide exactly one of file or sample_id",
            )
        try:
            source = (
                await save_upload(file, uuid4(), resolved_settings)
                if file is not None
                else catalog.resolve(sample_id or "")
            )
        except SourceRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        run_service = service(request)
        try:
            record = run_service.create(source)
        except Exception:
            source.cleanup()
            raise
        schedule(request, run_service.execute, record["run_id"], source)
        run_id = record["run_id"]
        return RunCreated(
            run_id=run_id,
            thread_id=record["thread_id"],
            status=record["status"],
            detail_url=f"/api/runs/{run_id}",
            events_url=f"/api/runs/{run_id}/events",
            stream_url=f"/api/runs/{run_id}/events/stream",
            review_url=f"/api/runs/{run_id}/review",
        )

    @application.get("/api/runs", response_model=RunList, tags=["runs"])
    def runs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> RunList:
        items = service(request).list(limit=limit, offset=offset)
        return RunList(items=[RunSummary.model_validate(item) for item in items])

    @application.get("/api/runs/{run_id}", response_model=RunDetail, tags=["runs"])
    def run_detail(request: Request, run_id: UUID) -> RunDetail:
        return RunDetail.model_validate(get_record_or_404(service(request), run_id))

    @application.get("/api/runs/{run_id}/events", response_model=EventList, tags=["events"])
    def run_events(
        request: Request,
        run_id: UUID,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> EventList:
        run_service = service(request)
        get_record_or_404(run_service, run_id)
        events = run_service.events(str(run_id), after_sequence=after)
        return EventList(items=[PublicEvent.model_validate(item) for item in events])

    @application.get("/api/runs/{run_id}/events/stream", tags=["events"])
    async def stream_run_events(
        request: Request,
        run_id: UUID,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        run_service = service(request)
        get_record_or_404(run_service, run_id)
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            idle_cycles = 0
            while True:
                if await request.is_disconnected():
                    break
                events = await asyncio.to_thread(
                    run_service.events,
                    str(run_id),
                    after_sequence=cursor,
                )
                if events:
                    idle_cycles = 0
                    for item in events:
                        cursor = max(cursor, int(item["sequence"]))
                        yield (
                            f"id: {item['sequence']}\n"
                            "event: run-event\n"
                            f"data: {json.dumps(item, default=str)}\n\n"
                        )
                else:
                    idle_cycles += 1
                record = await asyncio.to_thread(run_service.get, str(run_id))
                if record is None or record["status"] in STREAM_END_STATUSES:
                    remaining = await asyncio.to_thread(
                        run_service.events,
                        str(run_id),
                        after_sequence=cursor,
                    )
                    if not remaining:
                        break
                    continue
                if idle_cycles >= 60:
                    yield ": keep-alive\n\n"
                    idle_cycles = 0
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @application.post(
        "/api/runs/{run_id}/review",
        response_model=ReviewAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["reviews"],
    )
    async def review_run(
        request: Request,
        run_id: UUID,
        review: ReviewRequest,
    ) -> ReviewAccepted:
        run_service = service(request)
        record = get_record_or_404(run_service, run_id)
        if record["status"] != "PENDING_REVIEW":
            raise HTTPException(status_code=409, detail="Run is not pending review")
        claimed = run_service.begin_review(str(run_id))
        if claimed is None or claimed["status"] != "RUNNING":
            raise HTTPException(status_code=409, detail="Run review is already being processed")
        schedule(
            request,
            run_service.resume,
            str(run_id),
            decision=review.decision,
            actor=review.actor,
            rationale=review.rationale,
        )
        return ReviewAccepted(
            run_id=run_id,
            stream_url=f"/api/runs/{run_id}/events/stream",
        )

    @application.get("/api/runs/{run_id}/artifact", tags=["runs"])
    def audit_artifact(request: Request, run_id: UUID) -> Response:
        record = get_record_or_404(service(request), run_id)
        if record["status"] not in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Audit artifact is available after completion")
        body = json.dumps(record, indent=2, default=str).encode("utf-8")
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="run-{run_id}.json"'},
        )

    return application


app = create_app()
