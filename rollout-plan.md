# Rollout Plan — OCR/LLM SaaS (v1.1)

**Dokument:** Production rollout plan za OCR/LLM SaaS platformu zasnovanu na `OCR_LLM_SaaS_Arhitektura_v1.1.md`.

## 0) Cilj i obim
**Cilj:** Stabilan, bezbedan i merljiv produkcioni rollout pipeline‑a (preprocess → OCR+bbox → classification → structuring → reconciliation → validation → decision → review) sa 1.000+ dokumenata/sat u piku.

**Obim:** backend pipeline, review UI, infrastruktura, MLOps, observability, security, multi‑tenant governance.

## 1) Pretpostavke
- GLM‑OCR serviran preko vLLM (GPU pool, private subnet).
- Structuring LLM je odvojen (self‑host), JSON constrained decoding + schema validation.
- RabbitMQ + Redis + Postgres + MinIO u privatnom okruženju.

## 2) Milestone plan (P0 → P1 → P2)

### P0 — Kritični blokeri (bez ovoga nema produkcije)
**P0.1 Security/Privacy hardening**
- Privatni MinIO (nema public bucket access)
- Tajne iz vault/secret‑manager (nema placeholder creds)
- NetworkPolicy: inference nema public egress
**Acceptance:** svi dokumenti dostupni samo preko auth‑a ili presigned URL; penetration test bez data exposure.

**P0.2 DB schema lifecycle**
- Uvesti migracije (Alembic ili ekvivalent)
- `create_all()` isključen u prod
- Jedan izvor istine za shemu
**Acceptance:** fresh + upgrade deployment daju identičnu šemu, bez runtime mutacija.

**P0.3 Idempotentnost stage‑ova**
- OCR/Structured/Reconciliation jedinstveni per document
- Retry ne pravi duplikate
**Acceptance:** retry test pokazuje 1:1 record per stage.

**P0.4 Production gating**
- Feature flags za production rollout
- Canary dokumenti + rollback plan
**Acceptance:** moguće isključiti OCR/LLM bez downtime‑a.

---

### P1 — Funkcionalno/operativno stabilizovanje
**P1.1 Review UI end‑to‑end**
- bbox_evidence → UI highlight
- multipage navigacija
- PATCH `/documents/{id}/fields` wiring
**Acceptance:** operater vidi tačan bbox, može da edituje i vidi audit log.

**P1.2 Audit coverage**
- audit eventi za sve pipeline faze
**Acceptance:** kompletan audit trail po dokumentu.

**P1.3 Observability/Autoscaling**
- metrike API + worker + queue
- autoscaling po queue depth
**Acceptance:** alerti za backlog, SLA, error rate.

**P1.4 Tenant governance**
- per‑tenant schema/prompt routing
- RBAC operatora
**Acceptance:** tenant‑specific behavior bez koda.

---

### P2 — Kvalitet & dugoročnost
**P2.1 Unknown routing**
- eksplicitni manual classification flow
**P2.2 OCR traceability**
- source_span / stable block IDs
**P2.3 Test & Load Harness**
- regression suite + load test za 1.000+ doc/sat

## 3) Model plan
**OCR:** GLM‑OCR (vLLM). Tuning: batch, max‑seqs, gpu‑mem‑util.

**Structuring LLM shortlist:**
- Primary: Qwen3‑32B + constrained decoding (Outlines/Guidance)
- High accuracy: DeepSeek‑R1 Distill‑70B
- Fallback: DeepSeek‑R1 32B / Mistral‑Small‑24B

**Acceptance:** JSON schema compliance > 99% (validate+retry), Serbian accuracy target per doc type.

## 4) Infrastruktura (minimalni prod setup)
- **CPU cluster:** API + workers + RabbitMQ + Redis + Postgres + MinIO
- **GPU cluster:** GLM‑OCR + Structuring LLM (private subnet)
- **Monitoring:** Prometheus + Grafana + Loki + Alertmanager

## 5) Data governance
- Encrypted backups (AES‑256)
- Retention policy za OCR rezultate
- GDPR/PII policy (audit access log)

## 6) Rollout faze
**Faza 1: Internal Canary**
- 1 tenant, limit 50 docs/day
- manual review on for all docs

**Faza 2: Limited Beta**
- 3–5 tenants, production data
- auto‑post samo za high confidence

**Faza 3: Scale‑up**
- target 1.000+ docs/hour
- autoscaling on queue depth

**Faza 4: Full GA**
- SLA i billing aktivni

## 7) Rollback & Incident Plan
- Rollback: disable inference workers + route to manual queue
- Incident playbook: queue backlog, OCR failover, LLM failover

## 8) KPI / SLO
- Latency per doc (p50/p95)
- Error rate per stage
- Review queue age
- Auto‑post accuracy

---

## 9) Production Task Backlog (preostali taskovi)

### P0 — Kritično (blokira produkciju)
1. ✅ Zatvoriti MinIO public bucket pristup (ocr-documents/ocr-thumbnails)
2. ✅ Secrets management: ukloniti placeholder creds, uvesti Vault/K8s secrets + rotacija
3. ✅ NetworkPolicy: inference klaster bez public egress (private‑only)
4. ✅ Uvesti migracije (Alembic ili ekvivalent) i izbaciti `create_all()` iz prod
5. ✅ Jedinstven izvor istine za DB šemu (uskladiti / ukloniti init‑db.sql)
6. ✅ Idempotentnost stage‑ova: unique/upsert per document_id (OCR/Structured/Reconciliation)
7. ✅ Canary + rollback feature flags za inference pipeline

### P1 — Operativno stabilno
8. ✅ Review UI: bbox_evidence end‑to‑end highlight (bez text matching)
9. ✅ Review UI: multipage navigacija + page image fetch
10. ✅ Review UI: PATCH `/documents/{id}/fields` wiring + persist + audit
11. ✅ Approve/Reject workflow završava review lifecycle (status transition + audit)
12. ✅ Audit coverage za sve pipeline faze (OCR/classification/structuring/validation/review)
13. ✅ Observability: metrics za API + workers + queue depth
14. ✅ Autoscaling po queue depth (ne samo CPU)
15. ✅ Tenant‑specific schema/prompt routing
16. ✅ RBAC za operatere/role

### P2 — Kvalitet & dugoročnost
17. ✅ Eksplicitni “unknown” routing u manual classification queue
18. ✅ OCR traceability: stable block IDs (`block_id` = SHA-1 hash per block)
19. ✅ Regression test suite za sve doc tipove
20. ✅ Load test za 1.000+ docs/hour
21. ✅ Retry/idempotency testovi

**Owner:** Platform Team / ML Team / DevOps
**Status:** Draft (ready to operationalize)
