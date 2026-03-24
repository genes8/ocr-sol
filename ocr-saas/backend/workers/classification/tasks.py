"""Classification worker tasks - Regex-based document type routing."""

import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.config import settings
from api.models.db import Document, DocumentStatus, DocumentType, OCRResult
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Document classification result."""
    document_type: DocumentType
    confidence: float
    matched_patterns: list[str]
    reasoning: str


# Classification patterns for Serbian/Latin documents
CLASSIFICATION_PATTERNS = {
    DocumentType.INVOICE: {
        "keywords": [
            r"\bfaktura\b",
            r"\binvoice\b",
            r"\bračun\b",
            r"\bracun\b",
            r"\bPDV\b",  # VAT in Serbian
            r"\bporeski\s+broj\b",
            r"\bpib\b",
            r"\bukupno\b",  # total
            r"\biznos\b",  # amount
        ],
        "weight": 1.0,
    },
    DocumentType.PROFORMA: {
        "keywords": [
            r"\bproforma\b",
            r"\bponuda\b",
            r"\boferta\b",
            r"\bproposal\b",
            r"\bquote\b",
            r"\bpribilješka\b",
        ],
        "weight": 0.9,
    },
    DocumentType.DELIVERY_NOTE: {
        "keywords": [
            r"\bOtpremnica\b",
            r"\botpremnica\b",
            r"\bdelivery\s+note\b",
            r"\bdispatch\b",
            r"\bshipping\b",
            r"\bdelivery\s+order\b",
            r"\btrucking\s+note\b",
        ],
        "weight": 0.9,
    },
    DocumentType.CONTRACT: {
        "keywords": [
            r"\bUgovor\b",
            r"\bugovor\b",
            r"\bcontract\b",
            r"\bagreement\b",
            r"\bsporazum\b",
            r"\bzakup\b",  # lease
            r"\bnajam\b",  # rental
        ],
        "weight": 0.85,
    },
    DocumentType.BANK_STATEMENT: {
        "keywords": [
            r"\bizvod\b",
            r"\bBanka\b",
            r"\bbank statement\b",
            r"\btransaction\b",
            r"\bpromet\b",  # turnover
            r"\bstanje\b",  # balance
            r"\bUplatnica\b",
            r"\bIsplatnica\b",
        ],
        "weight": 0.85,
    },
    DocumentType.OFFICIAL_DOCUMENT: {
        "keywords": [
            r"\bРешење\b",
            r"\bresenje\b",  # decision
            r"\bzapisnik\b",  # minutes
            r"\bobrazac\b",  # form
            r"\bcertificate\b",
            r"\bpotvrda\b",  # certificate
            r"\bovlascenje\b",  # authorization
        ],
        "weight": 0.8,
    },
}


async def get_db_session() -> AsyncSession:
    """Get database session for workers."""
    from api.core.database import engine
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    return async_session()


def update_document_status(
    document_id: str,
    status: DocumentStatus,
    document_type: DocumentType | None = None,
    error_message: str | None = None,
) -> None:
    """Update document status and type in database."""
    import asyncio
    
    async def _update():
        session = await get_db_session()
        try:
            result = await session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = status
                doc.error_message = error_message
                if document_type:
                    doc.document_type = document_type
                await session.commit()
        finally:
            await session.close()
    
    asyncio.run(_update())


def classify_document(text: str) -> ClassificationResult:
    """Classify document based on extracted text.
    
    Args:
        text: Full OCR text
        
    Returns:
        ClassificationResult with document type and confidence
    """
    text_lower = text.lower()
    scores: dict[DocumentType, float] = {}
    matched: dict[DocumentType, list[str]] = {}
    
    for doc_type, config in CLASSIFICATION_PATTERNS.items():
        type_score = 0.0
        type_matches = []
        
        for keyword in config["keywords"]:
            pattern = re.compile(keyword, re.IGNORECASE)
            matches = pattern.findall(text)
            if matches:
                type_score += 0.2 * len(matches)  # Each match adds to score
                type_matches.extend(matches)
        
        if type_matches:
            # Normalize by weight
            scores[doc_type] = min(type_score * config["weight"], 1.0)
            matched[doc_type] = list(set(type_matches))  # Deduplicate
    
    if not scores:
        # Default to official document if no patterns match
        return ClassificationResult(
            document_type=DocumentType.OFFICIAL_DOCUMENT,
            confidence=0.3,
            matched_patterns=[],
            reasoning="No specific document type patterns detected",
        )
    
    # Get the highest scoring type
    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]
    
    # Normalize confidence
    max_possible_score = sum(0.2 * len(cfg["keywords"]) for cfg in CLASSIFICATION_PATTERNS.values())
    normalized_confidence = best_score / max_possible_score if max_possible_score > 0 else best_score
    
    return ClassificationResult(
        document_type=best_type,
        confidence=min(normalized_confidence, 1.0),
        matched_patterns=matched.get(best_type, []),
        reasoning=f"Matched {len(matched.get(best_type, []))} patterns for {best_type.value}",
    )


def extract_document_specific_fields(
    text: str,
    document_type: DocumentType,
) -> dict[str, Any]:
    """Extract document-type specific fields from text.
    
    Args:
        text: Full OCR text
        document_type: Detected document type
        
    Returns:
        Dict of extracted fields
    """
    fields: dict[str, Any] = {}
    text_lower = text.lower()
    
    # Serbian PIB (Tax ID) pattern
    pib_pattern = re.compile(r"\bPIB[:\s]*(\d{9,10})\b", re.IGNORECASE)
    pib_match = pib_pattern.search(text)
    if pib_match:
        fields["pib"] = pib_match.group(1)
    
    # Document number
    doc_num_pattern = re.compile(
        r"(?:Broj|Faktura|Invoice|No|Number)[:#\s]*([A-Z0-9/\-]+)",
        re.IGNORECASE
    )
    doc_num_match = doc_num_pattern.search(text)
    if doc_num_match:
        fields["document_number"] = doc_num_match.group(1)
    
    # Date patterns (Serbian format: DD.MM.YYYY)
    date_pattern = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b")
    dates = date_pattern.findall(text)
    if dates:
        fields["dates"] = [f"{d[0]}.{d[1]}.{d[2]}" for d in dates[:5]]
    
    # Amount patterns
    amount_pattern = re.compile(
        r"(?:Iznos|Total|Amount|Suma|Ukupno)[:#\s]*([\d.,]+)\s*(?:RSD|EUR|€|$)?",
        re.IGNORECASE
    )
    amounts = amount_pattern.findall(text)
    if amounts:
        fields["amounts"] = amounts[:5]
    
    # Company names (capitalized sequences)
    company_pattern = re.compile(r"\b([A-ZČĆŽŠĐ][a-zčćžšđ]+(?:\s+[A-ZČĆŽŠĐ][a-zčćžšđ]+)+)\b")
    companies = company_pattern.findall(text)
    if companies:
        fields["potential_company_names"] = companies[:3]
    
    return fields


@celery_app.task(bind=True, name="workers.classification.tasks.classify_document")
def classify_document_task(self, document_id: str, tenant_id: str, priority: int = 5) -> dict[str, Any]:
    """Classify a document based on OCR text.
    
    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID
        
    Returns:
        Classification result
    """
    start_time = time.time()
    logger.info(f"Starting classification for document {document_id}")
    
    try:
        # Update status
        update_document_status(document_id, DocumentStatus.CLASSIFIED)
        
        # Get OCR result
        import asyncio
        
        async def _get_ocr():
            session = await get_db_session()
            try:
                result = await session.execute(
                    select(OCRResult).where(
                        OCRResult.document_id == uuid.UUID(document_id)
                    )
                )
                return result.scalar_one_or_none()
            finally:
                await session.close()
        
        ocr_result = asyncio.run(_get_ocr())
        if not ocr_result:
            raise ValueError(f"No OCR result found for document {document_id}")
        
        # Classify document
        classification = classify_document(ocr_result.full_text)
        
        # Extract additional fields
        specific_fields = extract_document_specific_fields(
            ocr_result.full_text,
            classification.document_type,
        )
        
        # Update document with type
        update_document_status(
            document_id,
            DocumentStatus.STRUCTURING,
            document_type=classification.document_type,
        )

        # Trigger structuring (propagate priority for Feature 5)
        from workers.structuring.tasks import extract_structure
        extract_structure.apply_async(
            args=[document_id, tenant_id, priority],
            priority=priority,
        )

        processing_time = time.time() - start_time
        
        logger.info(
            f"Classification completed for document {document_id}: "
            f"type={classification.document_type.value}, "
            f"confidence={classification.confidence:.2f}"
        )
        
        return {
            "document_id": document_id,
            "status": "completed",
            "document_type": classification.document_type.value,
            "confidence": classification.confidence,
            "matched_patterns": classification.matched_patterns,
            "specific_fields": specific_fields,
            "reasoning": classification.reasoning,
            "processing_time": processing_time,
        }
        
    except Exception as exc:
        logger.exception(f"Classification failed for document {document_id}")
        update_document_status(
            document_id,
            DocumentStatus.VALIDATION_FAILED,
            error_message=str(exc),
        )
        
        raise self.retry(exc=exc, countdown=60, max_retries=3)
