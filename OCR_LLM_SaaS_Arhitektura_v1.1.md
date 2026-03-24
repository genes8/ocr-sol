# ARHITEKTURA — OCR / LLM SaaS Platforma

**Self-Hosted Private AI Processing za DMS Integraciju**

Verzija 1.1 | Mart 2026 | **POVERLJIVO**

---

## Sadržaj

1. [Pregled rešenja](#1-pregled-rešenja)
2. [Arhitektonski dijagram](#2-arhitektonski-dijagram)
3. [Slojevi sistema (7 slojeva)](#3-slojevi-sistema)
   - 3.1 Preprocessing
   - 3.2 GLM-OCR sa bbox/coordinates evidence
   - 3.3 Document Classification / Template Routing
   - 3.4 Structuring LLM
   - 3.5 Line Items Post-Processing i numerička rekoncilijacija
   - 3.6 Validacija
   - 3.7 Decision sa field-level confidence
4. [Infrastrukturni zahtevi](#4-infrastrukturni-zahtevi)
5. [Multi-Tenant pravila](#5-multi-tenant-pravila)
6. [Bezbednost i privatnost](#6-bezbednost-i-privatnost-podataka)
7. [Redovi i protok podataka](#7-redovi-i-protok-podataka)
8. [Dimenzionisanje — početni rollout](#8-dimenzionisanje--početni-rollout)
9. [Uska grla i mitigacije](#9-uska-grla-i-mitigacije)
10. [Anti-obrasci](#10-anti-obrasci--šta-ne-raditi)
11. [Preporučeni technology stack](#11-preporučeni-technology-stack)
12. [Sledeći koraci](#12-sledeći-koraci)

---

## 1. Pregled rešenja

Ovaj dokument definiše arhitekturu self-hosted OCR/LLM platforme koja se integriše sa postojećim SaaS DMS rešenjem za automatsku obradu faktura i ostalih poslovnih dokumenata. Sistem je dizajniran za multi-tenant okruženje sa peak opterećenjem od **1.000+ dokumenata na sat**.

### 1.1 Ključni ciljevi

- **Automatska OCR obrada** — GLM-OCR model za ekstrakciju teksta, layout-a i bounding box koordinata iz skeniranih dokumenata
- **Document Classification** — automatska klasifikacija tipa dokumenta (faktura, profaktura, otpremnica, ugovor...) pre strukturiranja
- **LLM strukturiranje** — self-hosted jezički model sa podrškom za srpski, za mapiranje OCR izlaza u strict JSON šemu po tipu dokumenta
- **Field-level confidence** — confidence score za svako pojedinačno polje, ne samo za ceo dokument
- **Line items rekoncilijacija** — specijalizovan post-processing za stavke faktura sa numeričkom verifikacijom
- **Multi-tenant izolacija** — per-tenant limiti, queue quote, audit trag
- **Self-hosted privatnost** — svi podaci ostaju u privatnoj infrastrukturi

### 1.2 Ključne pretpostavke

> OCR model (GLM-OCR) i structuring LLM su dva odvojena modela. OCR radi nad slikama i čuva bounding box koordinate. LLM za strukturiranje radi nad ekstrahovanim tekstom sa pozicijskim metapodacima.

> **[NOVO v1.1]** Sistem ne pretpostavlja da je svaki dokument faktura. Classification sloj rutira dokument na odgovarajuću JSON šemu i prompt strategiju pre LLM strukturiranja.

---

## 2. Arhitektonski dijagram

Sledeći dijagram prikazuje end-to-end tok obrade dokumenta (ažuriran na 7 slojeva):

```
Tenant/User upload
  ↓
DMS API ──────────────────→ MinIO / S3 private storage
  ↓
RabbitMQ
  ↓
Sloj 1: Preprocess Workers (PDF→image, deskew, split)
  ↓
Sloj 2: GLM-OCR GPU Pool (+bbox, +layout, +confidence per block)
  ↓
Raw OCR + Bbox Store
  ↓
Sloj 3: Document Classification / Template Routing
  │   ├─ faktura        → invoice_schema + invoice_prompt
  │   ├─ profaktura     → proforma_schema + proforma_prompt
  │   ├─ otpremnica     → delivery_schema + delivery_prompt
  │   ├─ ugovor         → contract_schema + contract_prompt
  │   ├─ bank statement → bank_stmt_schema + bank_prompt
  │   ├─ rešenje/dopis  → official_schema + official_prompt
  │   └─ nepoznat       → manual classification queue
  ↓
Sloj 4: Structuring LLM Pool (OCR → strict JSON + field-level confidence + bbox evidence)
  ↓
Sloj 5: Line Items Post-Processing (table normalization + numerička rekoncilijacija)
  ↓
Sloj 6: Validation (schema + business rules + field confidence evaluation)
  ↓
Sloj 7: Decision (field-level)
  ├─ AUTO   → sva polja iznad praga + rekoncilijacija PASS     → DMS Import Adapter
  ├─ REVIEW → neka polja ispod praga ILI rekoncilijacija WARN  → Review UI (bbox overlay)
  └─ MANUAL → kritično polje < 0.5 ILI rekoncilijacija FAIL    → Manual Entry Queue
                                                                      ↓
                                                                  Postgres → Audit / Metrics / Billing
```

**Ažuriran tok:** Upload → DMS API → MinIO + RabbitMQ → Preprocessing → GLM-OCR (+ bbox) → Classification / Routing → Structuring LLM → Line Items Post-Processing → Validation (field-level) → Decision → Postgres → Audit

---

## 3. Slojevi sistema

Sistem je organizovan u **sedam nezavisnih slojeva**. Svaki sloj ima jasno definisanu odgovornost i može se skalirati nezavisno. U odnosu na v1.0, dodata su tri nova sloja: Document Classification (3.3), Line Items Post-Processing (3.5) i prošireni su OCR (3.2), Validacija (3.6) i Decision (3.7).

### 3.1 Sloj 1 — Preprocessing

Priprema ulaznih dokumenata za OCR obradu:

- Konverzija PDF-a u slike (rasterizacija)
- Deskew — ispravljanje nagnutih skenova
- Quality check — odbacivanje neupotrebljivih slika
- Page split — razdvajanje višestraničnih dokumenata

### 3.2 Sloj 2 — GLM-OCR (GPU) sa Bounding Box Evidence

OCR sloj koristi GLM-OCR model (~0.9B parametara) servisan preko vLLM ili SGLang. Pored teksta, **obavezno čuva i prostorne koordinate** za svaki ekstrahovan element.

#### 3.2.1 Osnovni output

- Raw text ekstrakcija
- Layout blocks — identifikacija vizuelne strukture
- Tabele — prepoznavanje redova i kolona
- Kandidati za ključna polja (datum, iznos, PIB, itd.)

#### 3.2.2 Bbox / Coordinates Evidence

> **[NOVO v1.1]** Za Review UI nije dovoljno samo raw text. Operater mora da vidi odakle je model izvukao svako polje.

Svaki ekstrahovan element mora sadržati:

| Metapodatak | Opis |
|-------------|------|
| **bounding_box** | Koordinate [x1, y1, x2, y2] u pikselima ili normalizovane (0-1) za svaki blok teksta |
| **page_number** | Redni broj stranice iz koje je element ekstrahovan |
| **confidence** | OCR confidence score za taj konkretni blok (0.0 - 1.0) |
| **source_span** | Start/end pozicija u raw text-u — evidence za traceability |
| **block_type** | Tip: text, table_cell, header, footer, logo, stamp, signature |

> Bounding box evidence je kritičan za Review UI: operater klikom na polje u JSON-u vidi highlight na originalnom dokumentu. Bez ovoga review postaje ručno traženje po skenu.

#### 3.2.3 Primer OCR output strukture

| Polje | Primer vrednosti |
|-------|------------------|
| document_id | uuid-1234-5678 |
| page | 1 |
| block.text | "Faktura br. 2024-00142" |
| block.bbox | [0.12, 0.08, 0.65, 0.13] (normalizovane koordinate) |
| block.confidence | 0.97 |
| block.type | header |

### 3.3 Sloj 3 — Document Classification / Template Routing

> **[NOVO v1.1]** Novi sloj. Rešava problem da različiti tipovi dokumenata zahtevaju različite prompt strategije i JSON šeme.

Pre nego što dokument stigne do Structuring LLM-a, mora se klasifikovati i rutirati na odgovarajuću šemu i prompt template.

#### 3.3.1 Podržani tipovi dokumenata

| Tip dokumenta | JSON šema | Specifičnosti |
|----------------|-----------|---------------|
| Faktura | invoice_schema_v1.json | Stavke, PDV, totali, dobavljač |
| Profaktura | proforma_schema_v1.json | Slično fakturi, bez konačnog iznosa |
| Otpremnica | delivery_note_schema_v1.json | Stavke bez cena, transportni podaci |
| Ugovor | contract_schema_v1.json | Stranke, datumi, uslovi, potpisi |
| Bank statement | bank_stmt_schema_v1.json | Transakcije, saldo, period |
| Rešenje / dopis | official_doc_schema_v1.json | Institucija, predmet, pravni osnov |
| *Nepoznat* | — | Rutira se u manual classification queue |

#### 3.3.2 Strategije klasifikacije

Klasifikacija se može implementirati na tri načina, po rastućoj složenosti:

| Strategija | Kada koristiti | Prednosti / Mane |
|------------|----------------|------------------|
| **Pravila + regex** | Dokumenti sa jasnim markerima ("Faktura br.", "OTPREMNICA") | Brzo, bez GPU troškova. Krhko za nestandardne formate. |
| **Mali klasifikacioni model** | Veći volumen, mešoviti formati | Brz inference, mali footprint. Treba labeled dataset. |
| **LLM classifying pass** | Kompleksni / ambigvitetni dokumenti | Najfleksibilnije. Dodatni GPU trošak po dokumentu. |

> **Preporuka:** kombinovani pristup. Regex pravila pokrivaju 70-80% slučajeva, mali model za ostatak, LLM fallback samo za ambigvitetne.

#### 3.3.3 Template Routing

Nakon klasifikacije, dokument se rutira na:

- **JSON šema** — strict schema za taj tip dokumenta
- **Prompt template** — optimizovan prompt za taj tip sa few-shot primerima
- **Validation rules** — specifična pravila za taj tip (npr. faktura mora imati PIB, ugovor mora imati potpis)
- **Post-processing pipeline** — npr. line items rekoncilijacija samo za fakture/profakture

### 3.4 Sloj 4 — Structuring LLM

Poseban self-hosted LLM manji od OCR modela, optimizovan za srpski jezik. Radi nad ekstrakcijama teksta sa bbox metapodacima (ne nad slikama), što značajno smanjuje hardverske zahteve.

**Structuring LLM prima:**
- OCR text sa layout i bbox informacijama
- Klasifikovan tip dokumenta iz prethodnog sloja
- Odgovarajuća JSON šema i prompt template za taj tip

**Structuring LLM vraća:**
- Popunjenu JSON šemu sa svim ekstrahovanim poljima
- **Per-field confidence** za svako polje (0.0 - 1.0)
- **Per-field evidence** — bbox reference ka source bloku u OCR output-u
- Normalizovane formate (datuma, brojeva, valuta)

#### 3.4.1 Primer field-level output-a

| Polje | Vrednost | Conf. | Stranica | Bbox ref |
|-------|----------|-------|----------|----------|
| invoice_number | 2024-00142 | 0.98 | 1 | block_003 |
| invoice_date | 2024-11-15 | 0.95 | 1 | block_007 |
| supplier_pib | 100123456 | 0.72 | 1 | block_012 |
| total_amount | 45.800,00 | 0.91 | 2 | block_041 |
| vat_amount | 9.160,00 | 0.88 | 2 | block_043 |

> **[NOVO v1.1]** Field-level confidence omogućava da dokument sa visokim ukupnim confidence-om ipak ide na review ako jedno kritično polje (npr. PIB) ima nizak score.

### 3.5 Sloj 5 — Line Items Post-Processing

> **[NOVO v1.1]** Novi sloj. Kod faktura najveći problem nije zaglavlje nego stavke, količine, cene, poreska osnovica, PDV kolone i totali.

Ovaj sloj se aktivira samo za tipove dokumenata koji imaju line items (faktura, profaktura, otpremnica).

#### 3.5.1 Table Normalization

Sirove OCR tabele se normalizuju u standardni format:

- Identifikacija header reda i mapiranje kolona (r.br., opis, količina, jed. cena, osnovica, PDV, ukupno)
- Handling merged cells, multi-line stavki, fusnota u tabelama
- Normalizacija brojčanih formata: 1.234,56 (srpski) vs 1,234.56 (engleski)
- Čišćenje OCR artefakata iz numeričkih polja (O→0, l→1, ,→.)

#### 3.5.2 Numerička rekoncilijacija

Automatska provera numeričke konzistentnosti:

| Provera | Formula / Pravilo |
|---------|-------------------|
| **Stavka: količina × cena** | quantity × unit_price = line_total (± tolerancija zaokruživanja) |
| **Zbir stavki = osnovica** | Σ line_total = subtotal_amount |
| **PDV kalkulacija** | subtotal × vat_rate = vat_amount (± 0.01 tolerancija) |
| **Ukupno = osnovica + PDV** | subtotal_amount + vat_amount = total_amount |
| **Više PDV stopa** | Grupacija stavki po PDV stopi, rekoncilijacija po grupi |
| **Popusti / rabati** | Provera da je iznos posle popusta konzistentan |

#### 3.5.3 Rezultat rekoncilijacije

Svaka numerička provera generiše status:

- **PASS** — vrednosti se slažu unutar tolerancije
- **WARN** — malo odstupanje (zaokruživanje), ali prihvatljivo
- **FAIL** — neslaganje koje zahteva review

> Numerička rekoncilijacija je posebno kritična za srpske fakture gde se koriste i PDV od 20%, 10% i 0% na istoj fakturi, sa različitim osnovicama po grupi.

### 3.6 Sloj 6 — Validacija

Automatska validacija strukturiranog JSON-a pre upisa u DMS. Proširena sa field-level i document-type specifičnom validacijom.

#### 3.6.1 Generička validacija (svi tipovi)

- JSON schema validacija prema DMS šemi za taj tip dokumenta
- Obavezna polja provera (npr. faktura bez datuma = FAIL)
- Format validacija (datum, PIB format, IBAN)

#### 3.6.2 Invoice-specifična validacija

- Supplier matching — poređenje sa postojećom bazom dobavljača (PIB lookup)
- Numerička rekoncilijacija iz Sloja 5 (stavke, PDV, totali)
- Currency check — da li valuta odgovara zemlji dobavljača
- Duplikat detekcija — isti dobavljač + isti broj fakture + sličan iznos

#### 3.6.3 Field-level confidence evaluacija

> **[NOVO v1.1]** Confidence se evaluira po polju, ne samo po dokumentu. Ovo omogućava precizniju odluku u Sloju 7.

| Polje | Min confidence za auto-post |
|-------|----------------------------|
| invoice_number | 0.90 |
| invoice_date | 0.90 |
| supplier_name / PIB | 0.85 |
| total_amount | 0.95 |
| vat_amount | 0.90 |
| line_items (svaka stavka) | 0.80 |

> Pragovi su konfigurisani po tenant-u. Konzervativni tenant može postaviti sve na 0.95+, dok tenant sa visokim volumenom može prihvatiti niže pragove za non-critical polja.

### 3.7 Sloj 7 — Decision sa Field-Level Confidence

> **[NOVO v1.1]** Ažurirano: odluka se ne donosi samo na osnovu ukupnog confidence-a, nego kombinuje document-level, field-level i rekoncilijaciju.

| Odluka | Uslovi | Akcija |
|--------|--------|--------|
| **AUTO** | Sva polja iznad min confidence + rekoncilijacija PASS + schema valid + dokument klasifikovan | Automatski upis u DMS |
| **REVIEW** | Neka polja ispod min confidence ILI rekoncilijacija WARN ILI supplier ne postoji u bazi | Review UI sa highlighted problemima i bbox overlay |
| **MANUAL** | Kritično polje ispod 0.5 ILI rekoncilijacija FAIL ILI neklasifikovan dokument | Kompletno ručno unošenje |

**Review UI mora podržavati:** bbox overlay na originalnom skenu, inline editing po polju, one-click approve/reject, bulk actions za slične greške.

---

## 4. Infrastrukturni zahtevi

Infrastruktura je podeljena u dva logička klastera sa jasnim razdvajanjem CPU i GPU resursa.

### 4.1 Klaster 1 — Core Platform (CPU)

Aplikativni sloj koji ne zahteva GPU resurse:

| Komponenta | Instanca | Uloga |
|------------|----------|-------|
| DMS API | 2–3 instance | Aplikativni API za tenant pristup |
| PostgreSQL | 1 HA klaster | Glavna baza podataka, metadata, audit log, bbox store |
| Redis | 1 instanca | Cache, session, rate limiting |
| RabbitMQ | 1 klaster | Message queue za sve procesne redove |
| MinIO | Klaster | S3-kompatibilan privatni storage za dokumente |
| Classification service | 1–2 instance | Document type routing (CPU ili mali GPU) |
| Monitoring | 1 instanca | Prometheus + Grafana + Loki |

### 4.2 Klaster 2 — GPU Inference

Dediciran GPU pool za AI modele, bez javnog izlaza ka internetu:

| Komponenta | Instanca | Uloga |
|------------|----------|-------|
| GLM-OCR Worker Pool | 2–4 GPU noda | OCR inference + bbox extraction preko vLLM/SGLang |
| Structuring LLM Pool | 1–2 GPU noda | Text LLM za JSON strukturiranje sa field-level confidence |
| Interni API Gateway | 1 instanca | Routing između CPU i GPU klastera |

> GPU inference klaster treba da bude u privatnom subnetu/VLAN-u, bez public IP adresa, ako compliance to zahteva.

### 4.3 Deployment model

Preporučeni deployment model je **Kubernetes** sa razdvojenim CPU i GPU node pool-ovima:

- Lakše autoskaliranje worker-a na osnovu queue dubine
- Jasno odvajanje CPU i GPU node pool-ova
- Rolling deploy bez prekida servisa
- Centralizovani monitoring i resource quotas po servisu

**Alternativa za brži start:** VM + Docker + systemd + reverse proxy, ali samo kao prelazna faza do Kubernetes-a.

---

## 5. Multi-Tenant pravila

Pošto je SaaS DMS i servisira više kompanija, obavezna su sledeća pravila:

| Pravilo | Opis |
|---------|------|
| **tenant_id na svemu** | Svaki dokument, job, OCR rezultat, bbox, JSON output mora imati tenant_id |
| **Per-tenant concurrency** | Maksimalan broj istovremenih OCR/LLM job-ova po tenant-u |
| **Per-tenant queue quota** | Maksimalan broj dokumenata u redu čekanja po tenant-u |
| **Per-tenant confidence pragovi** | Svaki tenant konfiguriše min field-level confidence za auto-post |
| **Per-tenant šeme** | Custom JSON šeme i prompt template-i po tenant-u i tipu dokumenta |
| **Priority lane** | Enterprise korisnici dobijaju prioritetni red obrade |
| **Audit trag** | Kompletna istorija: upload, OCR, classification, structuring, validation, decision, review |
| **Billing metering** | Praćenje potrošnje po tenant-u (dokumenti, stranice, GPU sekunde) |

> Bez tenant limita, jedan veliki korisnik može da zauzme ceo inference pool i blokira ostale.

---

## 6. Bezbednost i privatnost podataka

Self-hosted ne znači samo da model hostuješ sam. Znači i da su storage, queue, logs, backups i review UI pod tvojom kontrolom.

### 6.1 Principi čuvanja podataka

- Originalni PDF/TIFF/JPG ostaje u privatnom MinIO storage-u
- OCR output (uključujući bbox) ostaje lokalno — nikad ne napušta infrastrukturu
- LLM structuring radi lokalno
- Embeddings/search (ako se uvede) — takođe lokalno
- Backup mora biti šifrovan (AES-256)

### 6.2 Tehničke mere

- Disk enkripcija na svim storage nodovima (LUKS / dm-crypt)
- TLS (mTLS preporučeno) unutar svih servisa
- RBAC po tenant-u i operateru
- Audit log za svaki pristup dokumentu
- GPU inference klaster u privatnom VLAN-u bez public IP-a
- Network policy — inference sloj nema izlaz ka internetu
- Secrets management — HashiCorp Vault ili K8s secrets sa enkripcijom

---

## 7. Redovi i protok podataka

Svaka faza obrade ima svoj zaseban red u RabbitMQ. Ažurirano sa novim redovima za classification i line items:

| Red | Funkcija |
|-----|----------|
| `ingest_queue` | Prijem dokumenata iz DMS API-ja |
| `preprocess_queue` | PDF rasterizacija, deskew, page split, quality check |
| `ocr_queue` | GLM-OCR GPU obrada — tekst + bbox + layout extraction |
| `classification_queue` | Document type classification i template routing |
| `structuring_queue` | LLM mapiranje OCR izlaza u JSON šemu sa field-level confidence |
| `line_items_queue` | Table normalization i numerička rekoncilijacija (samo za invoice-type) |
| `validation_queue` | Schema + business rules + field confidence evaluacija |
| `review_queue` | Dokumenti koji zahtevaju ljudsku verifikaciju (medium/low) |
| `dead_letter_queue` | Failed job-ovi za analizu i retry/manual intervenciju |

> Odvojeni redovi sprečavaju da spor preprocessing blokira GPU resurse, i omogućavaju precizno praćenje throughput-a po fazi.

---

## 8. Dimenzionisanje — početni rollout

Sledeće preporuke predstavljaju minimum za ozbiljan produkcioni setup sa 1.000+ dokumenata/sat u piku. Konačno dimenzionisanje zahteva realni benchmark.

### 8.1 Core CPU nodovi

| Parametar | Min | Preporučeno | Napomena |
|-----------|-----|-------------|----------|
| Broj nodova | 3 | 3 | API, queue, Redis, preprocessing, classification, pomoćni servisi |
| vCPU po nodu | 8 | 16 | Preprocessing + classification su CPU intenzivni |
| RAM po nodu | 32 GB | 64 GB | Redis + preprocessing + API buffering + bbox cache |

### 8.2 GPU OCR nodovi

| Parametar | Min | Komforno | Napomena |
|-----------|-----|----------|----------|
| Broj GPU nodova | 2 | 3–4 | Failover + rolling update bez prekida |
| VRAM po GPU | 24 GB | 32+ GB | GLM-OCR 0.9B + bbox extraction + vLLM overhead |
| RAM po nodu | 64 GB | 128 GB | Image preprocessing buffer + model serving |
| Storage | NVMe | NVMe | Brz pristup model weights i image cache |

### 8.3 Structuring LLM nodovi

| Parametar | Min | Komforno | Napomena |
|-----------|-----|----------|----------|
| Broj nodova | 1 | 2 | Manji zahtev nego OCR sloj |
| GPU klasa | Manji GPU | Manji GPU pool | Radi nad tekstom + bbox metapodacima, ne nad slikama |

### 8.4 Storage

- MinIO klaster ili drugi S3-compatible storage
- NVMe cache + veći persistent storage (HDD tier za archive)
- Lifecycle pravila: originali, OCR rezultati + bbox, thumbnails, review artefakti
- **Bbox storage:** ~2-5 KB po stranici, zanemarljivo u poređenju sa originalnim dokumentima

---

## 9. Uska grla i mitigacije

Nije samo model. Uska grla su obično van GPU inference-a:

| Usko grlo | Mitigacija |
|-----------|------------|
| PDF rasterizacija | Paralelni preprocessing worker-i, NVMe storage, optimizovane biblioteke (Poppler/MuPDF) |
| Multipage dokumenti | Page split u preprocessing-u, paralelna OCR obrada po stranici |
| Burst ingestion | RabbitMQ buffering, per-tenant rate limiting, autoscaling worker pool-ova |
| GPU scheduling | Kubernetes GPU scheduler, batch processing, request queuing u vLLM |
| Tabele / line items | Specijalizovani Sloj 5 sa table normalization i numeričkom rekoncilijacijom |
| Mešoviti tipovi dokumenata | Classification sloj (3.3) sa template routing pre strukturiranja |
| Review backlog | Prioritetni review queue, SLA monitoring, bbox overlay za brži review |

---

## 10. Anti-obrasci — šta ne raditi

Na osnovu iskustva sa sličnim sistemima:

- **"Jedan generički prompt rešava sve"** — različiti tipovi dokumenata zahtevaju različite šeme, promptove i validaciona pravila. Classification sloj ovo rešava.
- **Ista JSON šema za sve tipove** — faktura, otpremnica i ugovor nemaju ista polja. Template routing per document type.
- **Direktan OCR → final DB write** — bez validacije, rekoncilijacije i review sloja greške se nekontrolisano propagiraju
- **Document-level confidence umesto field-level** — dokument sa 0.95 ukupno može imati PIB sa 0.40. Field-level je obavezan.
- **Čuvanje samo finalnog JSON-a** — bez raw OCR + bbox evidence nema mogućnosti za debug, retrain, audit i review UI overlay
- **Jedan zajednički worker pool bez tenant limita** — jedan veliki korisnik blokira sve ostale
- **Oslanjanje na jedan GPU server** — single point of failure; minimum 2 GPU noda za failover
- **Sinhroni upload → OCR → JSON u istom requestu** — timeout-ovi i loš UX pri opterećenju
- **Ignorisanje numeričke rekoncilijacije** — OCR greška u jednoj cifri totala se ne može uhvatiti bez cross-check-a stavki

---

## 11. Preporučeni technology stack

| Kategorija | Tehnologija | Napomena |
|------------|-------------|----------|
| Orchestration | Kubernetes (K8s) | CPU + GPU node pool-ovi |
| Message Queue | RabbitMQ | 9 odvojenih redova po fazi obrade |
| Baza podataka | PostgreSQL (HA) | Metadata, audit, tenant, bbox reference |
| Cache | Redis | Session, rate limiting, cache |
| Storage | MinIO | S3-compatible: dokumenti, OCR + bbox output |
| OCR model | GLM-OCR (~0.9B) | Self-host, vLLM/SGLang, bbox extraction |
| Classification | Regex + mali model + LLM fallback | Kombinovani pristup za document routing |
| Structuring LLM | Manji text model (srpski) | Self-hosted, JSON + field confidence |
| Inference server | vLLM | OpenAI-compatible API, MTP/speculative decoding |
| Monitoring | Prometheus + Grafana + Loki | Metrike, alerting, logovi |
| Reverse proxy | Traefik / Nginx | SSL termination, routing |
| Secrets | HashiCorp Vault / K8s Secrets | Enkripcija kredencijala |

---

## 12. Sledeći koraci

Za implementaciju, predlažemo sledeći redosled:

| Faza | Aktivnost | Output |
|------|-----------|--------|
| **1** | Benchmark GLM-OCR na realnim fakturama sa bbox extraction | Throughput/accuracy baseline, bbox quality, GPU sizing |
| **2** | Izbor i testiranje structuring LLM-a za srpski sa field-level confidence | Odabran model, prompt template, JSON accuracy, confidence kalibracija |
| **3** | Document classification pipeline (regex + model + LLM) | Labeled dataset, classification accuracy per type, routing pravila |
| **4** | Server sizing za target infrastrukturu (Hetzner / on-prem / DC) | Broj mašina, RAM, GPU klasa, troškovi |
| **5** | K8s klaster setup sa CPU + GPU node pool-ovima | Funkcionalan klaster sa monitoring-om |
| **6** | End-to-end pipeline POC sa numeričkom rekoncilijacijom | POC: ingest → OCR+bbox → classify → structure → reconcile → DMS |
| **7** | Review UI sa bbox overlay + field-level editing | Operativni interfejs za assisted review |
| **8** | Load testing i optimizacija | Potvrda 1.000+ doc/sat u piku |
| **9** | Production rollout + monitoring | Go-live sa prvim tenant-ima |

---

*— Kraj dokumenta —*
