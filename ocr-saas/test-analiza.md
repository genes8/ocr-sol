# OCR SaaS — Test Analiza: 34 Prijavljenih Problema

Datum analize: 2026-03-25
Verifikacija: 2 explore agenta
Rezultat: **14 potvrđenih bugova (FIXED)**, 1 lažna pozitiva, 19 ostalih

---

## Pregled statusa

| ID  | Status          | Fajl                                     | Opis                                               |
|-----|-----------------|------------------------------------------|----------------------------------------------------|
| 1   | ✅ BUG (FIXED)  | `api/routes/webhooks.py:57`              | `async_sessionmaker` NameError — crash pri pozivu  |
| 2   | ✅ BUG (FIXED)  | `api/routes/documents.py:207`            | Silent MinIO exception — greška se guta            |
| 3   | ✅ BUG (FIXED)  | `api/routes/documents.py:164`            | Quota check TOCTOU race condition                  |
| 4   | ✅ BUG (FIXED)  | `api/core/database.py:74`                | Dead except block u `get_db_session()`             |
| 5   | ✅ BUG (FIXED)  | `api/core/security.py:127`               | `expires_at` se ne proverava za API ključeve       |
| 6   | ✅ BUG (FIXED)  | `api/routes/documents.py:566`            | Field path DoS — neograničena dubina               |
| 7   | ✅ BUG (FIXED)  | `frontend/src/components/field-editor.tsx:159` | Filter logika invertovana (`<` umesto `>=`)  |
| 8   | ✅ BUG (FIXED)  | `api/main.py:66`                         | Exception type leak u HTTP response body           |
| 9   | ✅ BUG (FIXED)  | Svi backend fajlovi                      | `datetime.utcnow()` deprecated (Python 3.12+)     |
| 10  | ✅ BUG (FIXED)  | `api/core/config.py:116`                 | CORS konfiguracija nedostaje validator             |
| 11  | ✅ BUG (FIXED)  | `api/routes/documents.py:651`            | Presigned URL expiry hardkodovan                   |
| 12  | ✅ BUG (FIXED)  | `api/routes/schemas.py:269`              | `TenantSettingsUpdate` prima arbitrary JSON        |
| 13  | ✅ BUG (FIXED)  | `api/models/db.py:175`                   | Cascade delete nedostaje za `Document.files`       |
| 14  | ❌ FALSE POSITIVE | `frontend/src/services/api.ts`         | Token refresh loop — nema beskonačne petlje        |
| 15  | ✅ BUG (FIXED)   | `api/routes/suppliers.py`              | RBAC nije primenjen na write operacije             |

---

## Detalji po bugu

---

### Bug 1 — KRITIČNO: `webhooks.py` NameError

**Status:** FIXED
**Fajl:linija:** `backend/api/routes/webhooks.py:57`
**Problem:**
```python
# BUGGY:
async with AsyncSession(async_sessionmaker) as db:
```
`async_sessionmaker` je klasa iz SQLAlchemy, ne instanca. Ovo uzrokuje `NameError` / pogrešno korišćenje — crash pri prvom pozivu webhook endpointa.

**Fix:**
```python
# FIXED:
from api.core.database import async_session_maker
async with async_session_maker() as db:
```

---

### Bug 2 — Silent MinIO Exception

**Status:** FIXED
**Fajl:linija:** `backend/api/routes/documents.py:207`
**Problem:**
MinIO `put_object()` poziv nije bio u try/except bloku. Greška u upload-u bi prouzrokovala da se dokument kreira u bazi ali fajl ne postoji u storage-u.

**Fix:**
```python
try:
    get_minio_client().put_object(...)
except Exception as e:
    logger.error("MinIO upload failed for document %s: %s", document_id, e)
    raise HTTPException(status_code=500, detail="Storage upload failed")
```

---

### Bug 3 — Quota Check TOCTOU Race Condition

**Status:** FIXED
**Fajl:linija:** `backend/api/routes/documents.py:164`
**Problem:**
`SELECT COUNT` i `INSERT` nisu atomični. Paralelni upload-ovi mogu prekoračiti tenant limit.

**Fix:**
Dodato `with_for_update()` na SELECT tenant-a:
```python
tenant_for_quota = await db.execute(
    select(Tenant).where(Tenant.id == tenant_id).with_for_update()
)
```

---

### Bug 4 — `database.py` Dead Except Block

**Status:** FIXED
**Fajl:linija:** `backend/api/core/database.py:74`
**Problem:**
`get_db_session()` imao dead except blok koji nikad ne može biti pogođen:
```python
session = async_session_maker()
try:
    return session      # ← return pre try bloka koji hvata
except Exception:
    await session.close()
    raise
```

Uz to, `get_db()` nije logovao exception pre rollback-a.

**Fix:**
- Uklonjen dead try/except iz `get_db_session()`
- Dodato `logger.exception(...)` u `get_db()` except bloku

---

### Bug 5 — `security.py` Ne Proverava Expired API Keys

**Status:** FIXED
**Fajl:linija:** `backend/api/core/security.py:127`
**Problem:**
`_resolve_auth()` proverava `is_active` ali ne proverava `expires_at`. Istekli API ključevi ostaju aktivni zauvek.

**Fix:**
```python
if api_key_obj:
    if api_key_obj.expires_at and api_key_obj.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="API key expired")
    return api_key_obj.tenant_id, api_key_obj.role
```

---

### Bug 6 — `documents.py` Field Path DoS

**Status:** FIXED
**Fajl:linija:** `backend/api/routes/documents.py:566`
**Problem:**
`PATCH /documents/{id}/fields` prima `"a.b.c.d.e...z"` bez limite dubine. Može uzrokovati memory exhaustion / ReDoS.

**Fix:**
```python
MAX_FIELD_DEPTH = 4
parts = field_path.split(".")
if len(parts) > MAX_FIELD_DEPTH:
    raise HTTPException(status_code=422, detail=f"Field path too deep: {field_path}")
for part in parts:
    if not part.isidentifier():
        raise HTTPException(status_code=422, detail=f"Invalid field name: {part}")
```

---

### Bug 7 — `field-editor.tsx` Filter Invertovan

**Status:** FIXED
**Fajl:linija:** `frontend/src/components/field-editor.tsx:159`
**Problem:**
```typescript
// BUGGY: pokazuje polja ISPOD praga kad se bira "High"
const matchesConfidence = filterConfidence === null || field.confidence < filterConfidence;
```
Selektovanjem "High (0.85)" filtrira prikazivalo je polja sa confidence < 0.85 (tj. medium + low), ne high.

**Fix:**
```typescript
// FIXED: prikazuje polja IZNAD praga
const matchesConfidence = filterConfidence === null || field.confidence >= filterConfidence;
```
Uz to, ažurirani su labeli opcija da jasno pokazuju šta svaka opcija prikazuje.

---

### Bug 8 — `main.py` Exception Type Leak

**Status:** FIXED
**Fajl:linija:** `backend/api/main.py:66`
**Problem:**
Global exception handler vraćao je `type(exc).__name__` u response body, što curi interne detalje (ime klase, stack trace indirectly).

**Fix:**
```python
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
```

---

### Bug 9 — `datetime.utcnow()` Deprecated

**Status:** FIXED
**Fajlovi:** Svi backend Python fajlovi
**Problem:**
`datetime.utcnow()` je deprecated od Python 3.12 i vraća naive datetime (bez timezone info), što može prouzrokovati probleme sa poređenjem sa aware datetime objektima.

**Fix:** Zameniti svuda sa `datetime.now(timezone.utc)`.
Za SQLAlchemy model defaults: `default=datetime.utcnow` → `default=lambda: datetime.now(timezone.utc)`.

Fajlovi koji su ažurirani:
- `api/routes/webhooks.py`
- `api/routes/documents.py`
- `api/routes/auth.py`
- `api/routes/suppliers.py`
- `api/routes/health.py`
- `api/models/db.py`
- `workers/validation/tasks.py`
- `workers/preprocessing/tasks.py`
- `workers/review/tasks.py`

---

### Bug 10 — CORS Config Nedostaje Validator

**Status:** FIXED
**Fajl:linija:** `backend/api/core/config.py:116`
**Problem:**
`CORS_ORIGINS` nije imao validator koji parsira comma-separated string iz env varijabli. Pydantic-settings podržava JSON liste, ali operateri često postavljaju `CORS_ORIGINS=https://a.com,https://b.com`.

**Fix:**
```python
@field_validator("CORS_ORIGINS", mode="before")
@classmethod
def parse_cors_origins(cls, v):
    if isinstance(v, str) and not v.startswith("["):
        return [origin.strip() for origin in v.split(",") if origin.strip()]
    return v
```
Dodata je i `PRESIGNED_URL_EXPIRY_SECONDS` config varijabla (Bug 11).

---

### Bug 11 — Presigned URL Expiry Hardkodovan

**Status:** FIXED
**Fajl:linija:** `backend/api/routes/documents.py:651`
**Problem:**
`expiry=3600` hardkodovano u `get_presigned_url()` pozivu.

**Fix:**
Dodata config varijabla:
```python
# config.py
PRESIGNED_URL_EXPIRY_SECONDS: int = 3600

# documents.py
url = get_presigned_url(doc_file.minio_path, expiry=settings.PRESIGNED_URL_EXPIRY_SECONDS)
```

---

### Bug 12 — `TenantSettingsUpdate` Nema Strict Schema

**Status:** FIXED
**Fajl:linija:** `backend/api/routes/schemas.py:269`
**Problem:**
`settings: dict[str, Any]` prima bilo koji JSON, uključujući arbitrarne ključeve koji mogu ugroziti sistem.

**Fix:**
```python
class TenantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_documents_per_month: int | None = None
    max_concurrent_processing: int | None = None
    allowed_document_types: list[str] | None = None
    confidence_thresholds: dict[str, float] | None = None
    plan: str | None = None
    schema_overrides: dict[str, Any] | None = None
    system_prompt: str | None = None

class TenantSettingsUpdate(BaseModel):
    settings: TenantSettings
```

---

### Bug 13 — Cascade Delete Nedostaje

**Status:** FIXED
**Fajl:linija:** `backend/api/models/db.py:175`
**Problem:**
`Document.files` relationship nije imao `cascade="all, delete-orphan"`. Brisanjem dokumenta ostaju orphan `DocumentFile` redovi u bazi.

**Fix:**
```python
files: Mapped[list["DocumentFile"]] = relationship(
    "DocumentFile", back_populates="document", lazy="selectin",
    cascade="all, delete-orphan"
)
```

**Napomena:** Alembic je već inicijalizovan (`backend/alembic/`) sa inicijalnom migracijom `0001_initial_schema.py`. `main.py` već ima uslovni `create_all()` samo za dev/staging.

---

## Lažni Pozitivi

### False Positive 14 — `api.ts` Token Refresh Loop

**Status:** FALSE POSITIVE
**Fajl:** `frontend/src/services/api.ts`
**Razlog:** Ovo je normalan OAuth2 refresh flow, nije beskonačna petlja. Token refresh logika detektuje 401 i refreshuje token jednom pre retry-a. (Napomena: u kodu nema eksplicitne "parallel refresh" zaštite.)

---

### Bug 15 — `suppliers.py` RBAC Write Operacije

**Status:** FIXED
**Fajl:** `backend/api/routes/suppliers.py`
**Problem:** `create_supplier` i `update_supplier` koristili su `get_current_tenant` bez `require_role`. Readonly API ključevi (role=`readonly`) mogli su da kreiraju i menjaju dobavljače.

**Fix:**
```python
# POST / i PATCH /{supplier_id}:
tenant_id: uuid.UUID = require_role("admin", "reviewer")
```
GET operacije (`list_suppliers`, `get_supplier`) ostaju sa `get_current_tenant` — readonly pristup je ispravan za čitanje.

---

## Ostali prijavljeni problemi (IDs 16–34)

> Ovi problemi su prijavljeni u test analizi, ali nisu detaljno dokumentovani u ovoj verziji izveštaja. Potrebno je dopuniti opis i status po stavci.

| ID  | Status                 | Fajl | Opis |
|-----|------------------------|------|------|
| 16  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 17  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 18  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 19  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 20  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 21  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 22  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 23  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 24  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 25  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 26  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 27  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 28  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 29  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 30  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 31  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 32  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 33  | ⚠️ NEDOKUMENTOVANO     | —    | —    |
| 34  | ⚠️ NEDOKUMENTOVANO     | —    | —    |

## Verifikacija Ispravki

| Bug | Test                                                                                  | Očekivani rezultat                              |
|-----|---------------------------------------------------------------------------------------|-------------------------------------------------|
| 1   | `POST /webhooks` endpoint poziv                                                       | Ne vraća 500 NameError                          |
| 5   | Kreirati API key, setovati `expires_at` u prošlost                                   | `401 API key expired`                           |
| 6   | `PATCH /documents/{id}/fields` sa `{"a.b.c.d.e.f": "x"}`                            | `422 Unprocessable Entity`                      |
| 3   | Paralelni upload-ovi (2 worker-a istovremeno) pri dostignutoj kvoti                  | Ne prekoračuju tenant limit                     |
| 2   | Pokvariti MinIO URL → pokrenuti upload                                                | Server vraća 500, log sadrži error poruku       |
| 8   | Baciti exception u handler → proveriti response body                                 | `{"detail": "Internal server error"}` bez type |
| 9   | `grep -r "utcnow" backend/`                                                           | 0 rezultata                                     |
| 7   | Field sa confidence 0.30, threshold 0.50 selektovan                                  | Field se prikazuje u "Low+" filteru             |
| 13  | `DELETE /documents/{id}` → `SELECT * FROM document_files WHERE document_id=...`       | 0 redova (cascade delete radi)                  |
