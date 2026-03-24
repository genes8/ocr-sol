# Changelog

## [Unreleased] — 2026-03-24

### Arhitekturne izmene (5 feature-a)

---

### Feature 1 — Dva odvojena vLLM servera (GLM-OCR + Structuring LLM)

**Problem:** OCR i structuring worker koristili su isti `VLLM_BASE_URL` / `VLLM_MODEL_NAME`.

**Izmene:**
- `backend/api/core/config.py` — dodati `STRUCTURING_LLM_BASE_URL` (default: `http://localhost:8002`), `STRUCTURING_LLM_MODEL_NAME`, `STRUCTURING_LLM_TIMEOUT`
- `backend/workers/structuring/tasks.py` — `call_llm_for_extraction()` sada koristi `STRUCTURING_LLM_*` konfiguraciju umesto `VLLM_*`
- `docker/docker-compose.yml` — novi servis `vllm-structuring` na portu `8002`, novi servis `worker-structuring` sa `STRUCTURING_LLM_BASE_URL`
- `k8s/gpu-deployments.yaml` — novi `vllm-structuring` Service + Deployment (text-only, bez GPU reservation); `worker-structuring` env blok zamenjen sa `STRUCTURING_LLM_*`

---

### Feature 2 — bbox Evidence u StructuredResult

**Problem:** Structuring LLM nije znao kojim OCR blokovima odgovaraju izdvojena polja. `StructuredResult` nije imao traceability.

**Izmene:**
- `backend/api/models/db.py` — `StructuredResult` dobio dve nullable JSON kolone: `bbox_evidence` i `supplier_lookup_result`
- `backend/workers/structuring/tasks.py`:
  - `build_extraction_prompt()` ubacuje indeksirani listing prvih 150 OCR blokova u prompt
  - LLM vraca `field_evidence: {field_name: block_index}` u odgovoru
  - `call_llm_for_extraction()` rezolvira indekse → stvarne bbox dict-ove (`{text, bbox, confidence, page}`)
  - `save_structured_result()` persistuje `bbox_evidence`
- `backend/api/routes/documents.py` — `GET /documents/{id}/result` vraca `structured_data.bbox_evidence`

---

### Feature 3 — Multiple VAT Rates Rekoncilijacija

**Problem:** `reconcile_line_items()` koristila jednu globalnu PDV stopu. Fakture sa mešanim stopama (0%, 10%, 20%) nisu bile ispravno rekoncilirane.

**Izmene:**
- `backend/workers/reconciliation/tasks.py` — potpuno prepisana `reconcile_line_items()`:
  - Grupira stavke po `vat_rate` per stavci (default 20% ako nije navedeno)
  - Računa `net_after_discount`, `vat_for_line` per stavci sa podrškom za `discount_amount` i `discount_pct`
  - Agregira `taxable_amount` i `vat_amount` po grupi
  - Poredi grupe sa `vat_breakdown` arrayjom iz extracted_data
  - `discrepancy_details.vat_groups` sadrži per-rate breakdown
  - Čita totale iz oba patha: `totals.grand_total` (schema) i `total_amount` (flat fallback)

---

### Feature 4 — Supplier Tabela + Matching + Duplikat Detekcija

**Problem:** Ne postoji registar dobavljača. Nije moguće detektovati nepoznate dobavljače niti duplikate faktura.

**Izmene:**
- `backend/api/models/db.py` — novi `Supplier` model (`suppliers` tabela) sa `UniqueConstraint("tenant_id", "pib")`; `Tenant.suppliers` relationship
- `backend/api/routes/suppliers.py` — novi fajl, 4 endpointa:
  - `GET    /api/v1/suppliers` — lista sa paginacijom i `is_active` filterom
  - `POST   /api/v1/suppliers` — kreiranje (PIB unique per tenant)
  - `GET    /api/v1/suppliers/{id}` — detalji
  - `PATCH  /api/v1/suppliers/{id}` — update
- `backend/api/routes/schemas.py` — dodati `SupplierCreate`, `SupplierUpdate`, `SupplierResponse`, `SupplierListResponse`, `TenantSettingsUpdate`
- `backend/api/main.py` — registrovan `/api/v1/suppliers` router
- `backend/workers/validation/tasks.py`:
  - `lookup_supplier()` — traži Supplier po PIB-u iz `extracted_data["supplier"]["pib"]`
  - `detect_duplicate()` — traži isti PIB + invoice_number u poslednjih 90 dana (primary); isti PIB + total_amount ±1% (secondary)
  - `determine_decision()` vraca `REVIEW / possible_duplicate` ili `REVIEW / supplier_not_found`
  - `supplier_lookup_result` se persistuje u `StructuredResult`
- `backend/api/routes/auth.py` — `PATCH /api/v1/auth/me/settings` za update `tenant.settings` (npr. `{"plan": "enterprise"}`)

**Performance napomena:**
```sql
CREATE INDEX idx_sr_invoice_num ON structured_results ((extracted_data->>'invoice_number'));
```

---

### Feature 5 — Priority Lane za Enterprise Tenante

**Problem:** Svi tenanti dele iste Celery queues sa FIFO redosledom. Enterprise tenanti ne dobijaju prioritet.

**Izmene:**
- `backend/api/core/config.py` — dodati `ENTERPRISE_TASK_PRIORITY=0`, `STANDARD_TASK_PRIORITY=5`
- `backend/workers/celery_app.py`:
  - `task_queues` zamenjeni sa `kombu.Queue` objektima koji imaju `queue_arguments={"x-max-priority": 10}`
  - Uklonjen nevalidni `task_queue_bindings` kljuc
- Svi worker task fajlovi (`preprocessing`, `ocr`, `classification`, `structuring`, `reconciliation`, `validation`) — dodati `priority: int = 5` parametar + `.apply_async(..., priority=priority)` u inter-worker pozivima
- `backend/api/routes/documents.py` — `upload_document()` cita `tenant.settings["plan"]`, postavlja `priority=0` za enterprise, `priority=5` za standard

---

## Deployment napomene

### RabbitMQ queue redeklartacija (obavezno za Feature 5)

RabbitMQ **ne dozvoljava promenu argumenata** (npr. `x-max-priority`) na vec postojecim queueovima.
Pre restarta aplikacije, svi postojeci queues moraju biti obrisani i pusteni da se automatski redeklarisu.

Pokrenuti skriptu:
```bash
./scripts/rabbitmq-reset-queues.sh
```

Ili rucno za svaki queue:
```bash
rabbitmqctl delete_queue preprocess_queue
rabbitmqctl delete_queue ocr_queue
rabbitmqctl delete_queue classification_queue
rabbitmqctl delete_queue structuring_queue
rabbitmqctl delete_queue reconciliation_queue
rabbitmqctl delete_queue validation_queue
rabbitmqctl delete_queue dead_letter_queue
```

Nakon brisanja, Celery workeri ce automatski rekreirati queues sa `x-max-priority=10` pri sledecem startu.

> ⚠️ Poruke koje se nalaze u queueovima u trenutku brisanja ce biti izgubljene. Preporucuje se da se svi workeri zaustave pre brisanja i da se saceka da se svi aktivni taskovi zavrse (drain).
