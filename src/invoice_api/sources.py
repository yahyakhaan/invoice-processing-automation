from __future__ import annotations

import mimetypes
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pdfplumber
from fastapi import UploadFile

from invoice_agent.config import ROOT, Settings
from invoice_api.schemas import SampleInvoice

ALLOWED_EXTENSIONS = {".txt", ".json", ".xml", ".csv", ".pdf"}
DEMO_SAMPLE_ID = "demo:vp-review"


class SourceRejected(ValueError):
    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedSource:
    path: Path | None
    label: str
    demo: bool = False
    cleanup_dir: Path | None = None

    def cleanup(self) -> None:
        if self.cleanup_dir is not None:
            shutil.rmtree(self.cleanup_dir, ignore_errors=True)


class SampleCatalog:
    def __init__(self, root: Path | None = None):
        self.root = (root or ROOT / "data" / "invoices").resolve()

    def list(self) -> list[SampleInvoice]:
        samples = [
            SampleInvoice(
                id=DEMO_SAMPLE_ID,
                name="VP review demo",
                format="demo",
                is_demo=True,
            )
        ]
        for path in sorted(self.root.iterdir()):
            if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
                samples.append(
                    SampleInvoice(
                        id=path.name,
                        name=path.name,
                        format=path.suffix.lower().lstrip("."),
                        size_bytes=path.stat().st_size,
                        content_url=f"/api/samples/{path.name}/content",
                    )
                )
        return samples

    def resolve(self, sample_id: str) -> PreparedSource:
        if sample_id == DEMO_SAMPLE_ID:
            return PreparedSource(path=None, label=DEMO_SAMPLE_ID, demo=True)
        if not sample_id or Path(sample_id).name != sample_id:
            raise SourceRejected("Unknown sample invoice", status_code=404)
        path = (self.root / sample_id).resolve()
        if path.parent != self.root or not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise SourceRejected("Unknown sample invoice", status_code=404)
        return PreparedSource(path=path, label=f"sample:{path.name}")

    def content_type(self, sample_id: str) -> str:
        source = self.resolve(sample_id)
        if source.demo or source.path is None:
            raise SourceRejected("The synthetic demo has no source file", status_code=404)
        return mimetypes.guess_type(source.path.name)[0] or "application/octet-stream"


async def save_upload(upload: UploadFile, run_id: UUID, settings: Settings) -> PreparedSource:
    filename = upload.filename or ""
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise SourceRejected("Upload filename is invalid")
    extension = Path(safe_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise SourceRejected(f"Unsupported file type. Allowed extensions: {allowed}", status_code=415)

    directory = Path(tempfile.mkdtemp(prefix=f"invoice-upload-{run_id}-"))
    destination = directory / safe_name
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise SourceRejected(
                        f"Upload exceeds the {settings.max_upload_mb} MB limit",
                        status_code=413,
                    )
                handle.write(chunk)
        if size == 0:
            raise SourceRejected("Upload is empty")
        if extension == ".pdf":
            try:
                with pdfplumber.open(destination) as document:
                    if len(document.pages) > settings.max_pdf_pages:
                        raise SourceRejected(
                            f"PDF exceeds the {settings.max_pdf_pages}-page limit",
                            status_code=413,
                        )
            except SourceRejected:
                raise
            except Exception as exc:
                raise SourceRejected("Uploaded PDF is invalid") from exc
        return PreparedSource(
            path=destination,
            label=f"upload:{safe_name}",
            cleanup_dir=directory,
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await upload.close()
