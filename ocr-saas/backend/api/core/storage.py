"""MinIO/S3 storage client for document storage."""

import io
from datetime import timedelta
from typing import BinaryIO

from minio import Minio
from minio.datatypes import Object

from api.core.config import settings

# Global MinIO client
minio_client: Minio | None = None


def get_minio_client() -> Minio:
    """Get MinIO client instance."""
    global minio_client
    if minio_client is None:
        minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
    return minio_client


async def ensure_buckets() -> None:
    """Ensure all required buckets exist."""
    client = get_minio_client()
    buckets = [
        settings.MINIO_BUCKET_DOCUMENTS,
        settings.MINIO_BUCKET_RESULTS,
        settings.MINIO_BUCKET_THUMBNAILS,
    ]
    for bucket in buckets:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def upload_document(
    file_data: BinaryIO,
    filename: str,
    content_type: str,
    tenant_id: str,
    document_id: str,
) -> str:
    """Upload a document to MinIO.

    Args:
        file_data: File-like object
        filename: Original filename
        content_type: MIME type
        tenant_id: Tenant UUID
        document_id: Document UUID

    Returns:
        Storage path in format: {tenant_id}/{document_id}/{filename}
    """
    client = get_minio_client()
    object_name = f"{tenant_id}/{document_id}/{filename}"

    file_data.seek(0, 2)  # Seek to end
    file_size = file_data.tell()
    file_data.seek(0)  # Reset to beginning

    client.put_object(
        bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
        object_name=object_name,
        data=file_data,
        length=file_size,
        content_type=content_type,
    )

    return object_name


def download_document(object_name: str) -> bytes:
    """Download a document from MinIO."""
    client = get_minio_client()
    response = client.get_object(
        bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
        object_name=object_name,
    )
    data = response.read()
    response.close()
    response.release_conn()
    return data


def get_presigned_url(
    object_name: str,
    bucket: str = settings.MINIO_BUCKET_DOCUMENTS,
    expiry: int = 3600,
) -> str:
    """Generate a presigned URL for temporary access.

    Args:
        object_name: Object name in bucket
        bucket: Bucket name
        expiry: URL expiry in seconds

    Returns:
        Presigned URL string
    """
    client = get_minio_client()
    return client.presigned_get_object(
        bucket_name=bucket,
        object_name=object_name,
        expires=timedelta(seconds=expiry),
    )


def delete_document(object_name: str) -> None:
    """Delete a document from MinIO."""
    client = get_minio_client()
    client.remove_object(
        bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
        object_name=object_name,
    )


def upload_result(
    data: bytes,
    document_id: str,
    tenant_id: str,
    result_type: str = "structured",
) -> str:
    """Upload processing result to MinIO.

    Args:
        data: Result data as bytes
        document_id: Document UUID
        tenant_id: Tenant UUID
        result_type: Type of result (structured, ocr, etc.)

    Returns:
        Storage path
    """
    client = get_minio_client()
    object_name = f"{tenant_id}/{document_id}/{result_type}.json"

    client.put_object(
        bucket_name=settings.MINIO_BUCKET_RESULTS,
        object_name=object_name,
        data=io.BytesIO(data),
        length=len(data),
        content_type="application/json",
    )

    return object_name


def upload_thumbnail(
    image_data: bytes,
    document_id: str,
    tenant_id: str,
    page: int = 1,
) -> str:
    """Upload document thumbnail.

    Args:
        image_data: Thumbnail image data
        document_id: Document UUID
        tenant_id: Tenant UUID
        page: Page number

    Returns:
        Storage path
    """
    client = get_minio_client()
    object_name = f"{tenant_id}/{document_id}/thumb_p{page}.jpg"

    client.put_object(
        bucket_name=settings.MINIO_BUCKET_THUMBNAILS,
        object_name=object_name,
        data=io.BytesIO(image_data),
        length=len(image_data),
        content_type="image/jpeg",
    )

    return object_name
