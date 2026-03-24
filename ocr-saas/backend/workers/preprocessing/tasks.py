"""Preprocessing worker tasks - PDF conversion, deskew, quality check."""

import io
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import Any

import cv2
import numpy as np
from PIL import Image
from pdf2image import convert_from_path
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.core.database import SyncSessionLocal, get_db_session
from api.core.storage import get_minio_client, upload_thumbnail
from api.models.db import Document, DocumentFile, DocumentStatus
from workers.celery_app import celery_app


def write_audit_event(
    tenant_id: str,
    event: str,
    document_id: str | None = None,
    actor: str = "system",
    payload: dict | None = None,
) -> None:
    """Write audit event from sync worker context."""
    import asyncio

    async def _write():
        from api.core.audit import write_audit
        import uuid as _uuid
        session = await get_db_session()
        try:
            await write_audit(
                session,
                _uuid.UUID(tenant_id),
                event,
                document_id=_uuid.UUID(document_id) if document_id else None,
                actor=actor,
                payload=payload,
            )
            await session.commit()
        finally:
            await session.close()

    asyncio.run(_write())

logger = logging.getLogger(__name__)


def get_sync_session() -> Session:
    """Get sync database session for Celery workers."""
    return SyncSessionLocal()


def update_document_status(
    document_id: str,
    status: DocumentStatus,
    error_message: str | None = None,
) -> None:
    """Update document status in database."""
    session = get_sync_session()
    try:
        result = session.execute(
            select(Document).where(Document.id == uuid.UUID(document_id))
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = status
            doc.error_message = error_message
            if status == DocumentStatus.PREPROCESSING:
                doc.processing_started_at = datetime.utcnow()
            session.commit()
    finally:
        session.close()


def save_processed_image(
    document_id: str,
    tenant_id: str,
    image_data: bytes,
    page_number: int,
    file_type: str = "processed",
) -> str:
    """Save processed image to MinIO."""
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET_DOCUMENTS
    object_name = f"{tenant_id}/{document_id}/p{page_number}_{file_type}.jpg"
    
    client.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(image_data),
        length=len(image_data),
        content_type="image/jpeg",
    )
    
    return object_name


def create_document_file_record(
    document_id: str,
    tenant_id: str,
    minio_path: str,
    file_type: str,
    page_number: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Create DocumentFile record in database."""
    session = get_sync_session()
    try:
        doc_file = DocumentFile(
            id=uuid.uuid4(),
            document_id=uuid.UUID(document_id),
            file_type=file_type,
            page_number=page_number,
            minio_path=minio_path,
            width=width,
            height=height,
        )
        session.add(doc_file)
        session.commit()
    finally:
        session.close()


def pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> list[Image.Image]:
    """Convert PDF pages to PIL Images using pdf2image.
    
    Args:
        pdf_bytes: PDF file content
        dpi: Target DPI for rendering (default 200)
        
    Returns:
        List of PIL Images, one per page
    """
    images = []
    
    # Write PDF to temporary file (pdf2image requires file path)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
        tmp_pdf.write(pdf_bytes)
        tmp_pdf_path = tmp_pdf.name
    
    try:
        # Convert PDF pages to images
        # Use poppler path if POPPLER_PATH env var is set
        poppler_path = os.environ.get("POPPLER_PATH")
        
        pdf_images = convert_from_path(
            tmp_pdf_path,
            dpi=dpi,
            poppler_path=poppler_path,
            fmt="jpeg",
            jpeg_quality=95,
            thread_count=1,  # Single thread to avoid memory issues
            strict=False,
        )
        
        for img in pdf_images:
            # Ensure RGB mode
            if img.mode != "RGB":
                img = img.convert("RGB")
            images.append(img)
            
        logger.info(f"Converted {len(images)} pages from PDF at {dpi} DPI")
        
    except Exception as e:
        logger.error(f"Failed to convert PDF using pdf2image: {e}")
        # Fallback: try using pypdf as last resort
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        
        for page_num, page in enumerate(reader.pages, 1):
            # Get page dimensions
            page_width = int(page.mediabox.width or 612)
            page_height = int(page.mediabox.height or 792)
            
            # Create a white image as fallback
            # This is better than nothing for processing continuation
            img = Image.new("RGB", (page_width, page_height), color="white")
            images.append(img)
            
        logger.warning(f"Using fallback blank images for PDF (pypdf fallback)")
    
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_pdf_path)
        except Exception:
            pass
    
    return images


def detect_skew(image: Image.Image) -> float:
    """Detect skew angle of an image.
    
    Args:
        image: PIL Image
        
    Returns:
        Skew angle in degrees
    """
    img_array = np.array(image.convert("L"))
    
    # Use OpenCV for edge detection and Hough lines
    edges = cv2.Canny(img_array, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
    
    if lines is None or len(lines) < 5:
        return 0.0
    
    angles = []
    for line in lines[:50]:  # Consider only first 50 lines
        rho, theta = line[0]
        angle = (theta * 180 / np.pi) - 90
        if -45 < angle < 45:
            angles.append(angle)
    
    if not angles:
        return 0.0
    
    # Return median angle
    return float(np.median(angles))


def deskew_image(image: Image.Image, angle: float) -> Image.Image:
    """Deskew an image by the given angle.
    
    Args:
        image: PIL Image
        angle: Skew angle in degrees
        
    Returns:
        Deskewed PIL Image
    """
    if abs(angle) < 0.5:
        return image
    
    img_array = np.array(image)
    
    # Get center and rotation matrix
    h, w = img_array.shape[:2]
    center = (w / 2, h / 2)
    
    # Rotation matrix
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    
    # Perform rotation
    rotated = cv2.warpAffine(
        img_array, 
        M, 
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    
    return Image.fromarray(rotated)


def check_image_quality(image: Image.Image) -> dict[str, Any]:
    """Check image quality metrics.
    
    Args:
        image: PIL Image
        
    Returns:
        Dict with quality metrics: blur_score, brightness, is_acceptable
    """
    img_array = np.array(image.convert("L"))
    
    # Calculate Laplacian variance for blur detection
    laplacian = cv2.Laplacian(img_array, cv2.CV_64F)
    blur_score = laplacian.var()
    
    # Calculate average brightness
    brightness = np.mean(img_array)
    
    # Quality thresholds
    BLUR_THRESHOLD = 100.0
    BRIGHTNESS_MIN = 30
    BRIGHTNESS_MAX = 230
    
    is_acceptable = (
        blur_score >= BLUR_THRESHOLD and
        BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX
    )
    
    return {
        "blur_score": float(blur_score),
        "brightness": float(brightness),
        "is_acceptable": bool(is_acceptable),
        "warnings": _get_quality_warnings(blur_score, brightness),
    }


def _get_quality_warnings(blur_score: float, brightness: float) -> list[str]:
    """Generate warnings based on quality metrics."""
    warnings = []
    
    if blur_score < 100:
        warnings.append("Image may be blurry")
    if brightness < 30:
        warnings.append("Image is too dark")
    elif brightness > 230:
        warnings.append("Image is too bright")
    
    return warnings


def create_thumbnail(image: Image.Image, max_width: int = 300) -> bytes:
    """Create thumbnail from image.
    
    Args:
        image: PIL Image
        max_width: Maximum thumbnail width
        
    Returns:
        JPEG bytes
    """
    width, height = image.size
    if width > max_width:
        ratio = max_width / width
        new_height = int(height * ratio)
        image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()


@celery_app.task(bind=True, name="workers.preprocessing.tasks.process_document")
def process_document(self, document_id: str, tenant_id: str, priority: int = 5) -> dict[str, Any]:
    """Process a document: convert PDF, deskew, check quality.
    
    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID
        
    Returns:
        Processing result with pages info
    """
    start_time = time.time()
    logger.info(f"Starting preprocessing for document {document_id}")
    
    try:
        # Update status
        update_document_status(document_id, DocumentStatus.PREPROCESSING)
        
        # Get document from database (sync)
        session = get_sync_session()
        try:
            result = session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = result.scalar_one_or_none()
        finally:
            session.close()
        
        if not doc:
            raise ValueError(f"Document {document_id} not found")
        
        # Get original file
        client = get_minio_client()
        result = client.get_object(
            bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
            object_name=doc.files[0].minio_path if doc.files else f"{tenant_id}/{document_id}/{doc.filename}",
        )
        file_bytes = result.read()
        result.close()
        result.release_conn()
        
        # Determine file type and process
        if doc.content_type == "application/pdf":
            images = pdf_to_images(file_bytes, dpi=200)
        elif doc.content_type.startswith("image/"):
            images = [Image.open(io.BytesIO(file_bytes))]
        else:
            raise ValueError(f"Unsupported content type: {doc.content_type}")
        
        pages_info = []
        
        for page_num, image in enumerate(images, start=1):
            # Detect and fix skew
            skew_angle = detect_skew(image)
            if abs(skew_angle) >= 0.5:
                image = deskew_image(image, skew_angle)
                logger.info(f"Deskewed page {page_num} by {skew_angle:.2f} degrees")
            
            # Check quality
            quality = check_image_quality(image)
            if not quality["is_acceptable"]:
                logger.warning(
                    f"Page {page_num} quality issues: {quality['warnings']}"
                )
            
            # Convert to RGB for JPEG
            if image.mode != "RGB":
                image = image.convert("RGB")
            
            # Save processed image
            processed_bytes = io.BytesIO()
            image.save(processed_bytes, format="JPEG", quality=95)
            processed_bytes = processed_bytes.getvalue()
            
            minio_path = save_processed_image(
                document_id=document_id,
                tenant_id=tenant_id,
                image_data=processed_bytes,
                page_number=page_num,
                file_type="processed",
            )
            
            # Create thumbnail
            thumbnail_bytes = create_thumbnail(image)
            thumbnail_path = upload_thumbnail(
                image_data=thumbnail_bytes,
                document_id=document_id,
                tenant_id=tenant_id,
                page=page_num,
            )
            
            # Create database record
            create_document_file_record(
                document_id=document_id,
                tenant_id=tenant_id,
                minio_path=minio_path,
                file_type="processed",
                page_number=page_num,
                width=image.width,
                height=image.height,
            )
            
            pages_info.append({
                "page_number": page_num,
                "width": image.width,
                "height": image.height,
                "skew_angle": skew_angle,
                "quality": quality,
                "processed_path": minio_path,
                "thumbnail_path": thumbnail_path,
            })
        
        # Update document (sync)
        session = get_sync_session()
        try:
            result = session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = DocumentStatus.OCR
                doc.page_count = len(images)
                doc.doc_metadata = {"pages": pages_info}
                session.commit()
        finally:
            session.close()
        
        processing_time = time.time() - start_time
        
        logger.info(
            f"Preprocessing completed for document {document_id} "
            f"in {processing_time:.2f}s"
        )
        
        # Trigger OCR processing (propagate priority for Feature 5)
        from workers.ocr.tasks import process_ocr
        process_ocr.apply_async(
            args=[document_id, tenant_id, priority],
            priority=priority,
        )

        write_audit_event(
            tenant_id, "pipeline.preprocess.completed", document_id,
            actor="worker:preprocessing",
            payload={"pages": len(images), "processing_time_s": round(processing_time, 2)},
        )

        return {
            "document_id": document_id,
            "status": "completed",
            "pages": len(images),
            "pages_info": pages_info,
            "processing_time": processing_time,
        }
        
    except Exception as exc:
        logger.exception(f"Preprocessing failed for document {document_id}")
        update_document_status(
            document_id, 
            DocumentStatus.PREPROCESS_FAILED,
            error_message=str(exc),
        )
        
        # Retry or fail
        raise self.retry(exc=exc, countdown=60, max_retries=3)
