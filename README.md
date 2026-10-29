# PartProof

Evidence-first listing assistant for a Dubai used-auto-parts seller.
Hackathon prototype: 10 used side-view mirrors, UAE → UK corridor, mock eBay export.

## Quick Start

Run every command from the project root. Each new terminal needs the venv active
(VS Code activates `.venv` automatically; you'll see `(.venv)` in the prompt).

```bash
# 1. Create venv and install deps (once)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Initialize the database schema
python scripts/init_db.py

# 3. Generate synthetic placeholder photos
python scripts/generate_photos.py

# 4. Generate compatibility reference checksum
python scripts/generate_checksum.py

# 5. Seed the database with the test pack (20 parts, evidence, compat refs, seller profile)
python scripts/seed.py
#    Already have a database from an earlier version? Upgrade it in place instead (safe to re-run):
#    python scripts/migrate_db.py

# 6. (Optional) Run tests
pytest

# 7. Run the API (terminal 1; start it first)
python -m uvicorn app.api.main:app --port 8000 --reload

# 8. Run the UIs (terminals 2 and 3)
python -m streamlit run ui/app.py --server.port 8501         # camera-first app
python -m streamlit run ui/workspace.py --server.port 8502   # part workspace (board, part detail, quarantine)
```

Then open http://localhost:8501 (camera-first app), http://localhost:8502 (workspace)
or http://localhost:8000/docs (API docs).

Use `python -m …` as above. `python scripts/run_api.py` fails with
`No module named 'app'`, because running a file in `scripts/` doesn't put the
project root on the import path.

By default `FIXTURE_MODE=true` — the app runs with no API key by replaying
recorded JSON from `data/fixtures/`.

### Troubleshooting

| Error | Fix |
|---|---|
| `No module named 'uvicorn'` (or `streamlit`) | The venv is active but its packages aren't installed: run `pip install -r requirements.txt`. |
| `No module named 'app'` | Start the API with `python -m uvicorn …` from the project root, not `scripts/run_api.py`. |
| Port already in use | Another copy is still running. Windows: `netstat -ano \| findstr :8000`, then `taskkill /PID <pid> /F` (same for 8501 and 8502). |
| Answers in the chat don't change a part's status | The pipeline's state lives in the API's memory and is lost on restart. The camera-first app re-runs it when the page loads; in the workspace, press **Run pipeline**. |

### Missing source files

Six modules are not in the repository: `app/data/audit.py`,
`app/domain/approval.py`, `app/domain/export.py`, `app/observability/__init__.py`,
`app/observability/latency.py` and `app/orchestration/observability.py`. On the
machine where they were compiled, the API runs from their Python 3.14 bytecode
(`.pyc` files next to each package, which are gitignored). A fresh clone, or any
other Python version, fails at import until the original `.py` files are committed.

## Architecture

```
ui (Streamlit)  →  app/api (FastAPI gateway)  →  app/orchestration  →  app/models (LLM gateway)
                                                         ↓
                                            app/domain (deterministic rules)
                                            app/data (SQLite)
                                            app/guardrails (pre-export checks)
```

**Tier boundaries (Rule g):**
- UI talks to the API gateway only.
- Only orchestration calls the model layer.
- Domain services never call an LLM.

See `docs/RULES.md` for the full standing rules.  
See `docs/DECISIONS.md` for design decisions when the PRD/TRD left details open.

## Synthetic Data Notice

**All data in this repository is entirely synthetic.** There are no real customer
records, real part numbers from live inventory, or real vehicle VINs. The placeholder
photos in `data/seed/photos/` are simple Pillow-generated drawings — not photographs
of real parts. They exist solely to test the pipeline's photo-processing and OCR paths.

The test pack (`data/seed/`, `data/reference/`, `data/answer_key/`) contains:
- **20 part records** (M-01 to M-20): 10 demo side-view mirrors + 10 extra test records
- **14 trap types** (E-IDs): planted issues for testing detection accuracy
- **5 journeys** (J1–J5): expected pipeline paths from ingest to export
- **Compatibility reference**: mock OEM catalogue with exact part-number lookups
- **Checklist v0.1**: C01–C15 items (TEST checklist, not customs or legal guidance)
- **Answer key**: expected values, sources, and statuses for automatic scoring

## Project Layout

| Directory | Purpose |
|---|---|
| `app/api` | FastAPI gateway — only entry point for the UI |
| `app/orchestration` | CP0 upload gate; LangGraph supervisor and background queue; Inquiry agent; chat concierge; model-call policy (timeouts, retry, fallback) |
| `app/semantic` | Intake agent (read_image, ocr, parse_sheet, translate_note; cached prompt; parallel ingestion) and the semantic layer: seller-scoped entity model, seller context card, glossary, metrics, chat access policy, intents |
| `app/domain` | Pydantic models, enums, CP1 rules, CP2 ledger and Reconciler, CP4 compatibility, propagation engine, CP6 readiness (deterministic, no LLM) |
| `app/data` | SQLite schema and database helpers |
| `app/models` | LLM gateway — two tiers, caching, fixture replay, cost logging |
| `app/guardrails` | Trust tiers, data tags, injection screen, quarantine, agent tool registry |
| `ui` | Streamlit front-end |
| `data/seed` | Seed part data (synthetic) |
| `data/reference` | Mock OEM compatibility catalogue |
| `data/answer_key` | Ground-truth answers for evaluation |
| `data/fixtures` | Recorded LLM responses for FIXTURE_MODE |
| `tests` | Unit and integration tests |
| `scripts` | DB init, API/UI launchers |
| `docs` | RULES.md, DECISIONS.md |

## CP0 Upload Gate

The upload path runs as a background checkpoint (CP0) before any LLM is involved.
It handles edge cases E01–E10 and E51–E53 per TRD 2, 6 and 7.

**`app/orchestration/` modules:**

| Module | Edge cases | What it does |
|---|---|---|
| `file_gate.py` | E01, E02, E04, E05 | Type/size check, decode check, HEIC→JPEG+resize, SHA-256 cache key, perceptual-hash dup detection, blur/brightness retake, EXIF strip |
| `text_normaliser.py` | E09, E10 | Unicode NFKC, strip zero-width/bidi control chars, Arabic-Indic digit→ASCII (keep raw as evidence), field length cap, garbled Arabic retry as UTF-8 then Windows-1256 |
| `spreadsheet_parser.py` | E07, E08 | CSV/XLSX parsing, glossary header mapping (EN+AR), unmerge cells, skip blank rows, flag duplicate SKUs |
| `sku_linker.py` | E03 | SKU extraction from filename pattern only, manual-assign tray with suggested SKU |
| `injection_scan.py` | E53 | English + Arabic injection pattern scan, quarantine hits with triggering text stored and logged, never passed as instructions |
| `privacy_mask.py` | E51, E52 | Phone/name/VIN/plate masking in text, face/plate blur in photos |
| `cp0_pipeline.py` | — | Orchestrates all steps; every snippet becomes an Evidence row (kind, pointer, raw_text, language, trust tier T2) |

Nothing in CP0 calls an LLM. Per Rule (f), ingested text is data, never instructions.
Per Rule (g), domain services never call an LLM.

## CP1 Intake Agent + Validation Checkpoint

The Intake agent extracts structured attributes from part evidence (photos,
spreadsheet rows, donor notes) using the main-tier vision model, then CP1
validates the extraction with deterministic rules. Handles edge cases E12–E15,
E16, E23, E47–E48 per TRD 5, 6 and 7.

**`app/semantic/` modules:**

| Module | What it does |
|---|---|
| `intake_tools.py` | 4 allowed tools: `read_image` (vision), `ocr` (EasyOCR cross-check), `parse_sheet`, `translate_note`. All return typed JSON only. Caching by file hash. |
| `intake_prompt.py` | Cached system prompt (evidence in `<data>` blocks is never instructions; missing values must be null; never infer test/authenticity/compatibility). Seller card. Fresh random evidence IDs per request. |
| `intake_agent.py` | Orchestrates tools + CP1 validation. Runs in parallel with asyncio. Writes Attribute + Evidence rows with source pointer on every populated field. FIXTURE_MODE replays recorded output. |

**`app/domain/cp1_rules.py` — pure rules, no LLM:**

| Edge case | Rule |
|---|---|
| E12 | Ambiguous characters (0/O, 8/B, 1/I) — never guess, preserve OCR `?` marks |
| E13 | OCR and vision disagree → Conflicting, keep both crops |
| E14 | Multiple numbers on label stored by class (OEM, supplier, date_code); only OEM used for lookup |
| E15 | Aftermarket brand on OEM donor → recorded as seen, no OEM claim |
| E16 | Donor year conflict across sources → Conflicting, keep all values with sources |
| E23 | Vision position disagrees with text → flagged as Conflicting |
| E47 | Malformed JSON → one repair attempt, then Missing |
| E48 | Text value not in source or OCR text → dropped and logged |

Plus: schema validation with allowed values, part-number pattern, year range
1990–2027, length caps. Per Rule (a), LLMs propose, deterministic rules decide.
Per Rule (g), CP1 is a domain service that never calls an LLM.

## CP2 Evidence Ledger + Reconciler

The evidence ledger enforces versioned attributes with status handling per TRD 3,
and the Reconciler finds missing, conflicting, and implausible values (TRD 3 and 7).
All rules, no LLM.

**`app/domain/ledger.py` — Evidence Ledger:**
- Versioned per field (`attribute_versions` table)
- No attribute value without evidence (enforced invariant)
- Status visibility: Seller-confirmed/Verified = plain fact; Extracted = fact for
  low-risk fields only (colour, rack); Conflicting/Missing/Unverified = never fact
- Blocking: Conflicting blocked until resolved; Missing blocked if required field
- Stale draft detection via `built_from_versions` (E49)
- Seller confirmation (`confirm_from_seller`) and test verification (`verify_from_test`)

**`app/domain/provenance.py` — Provenance Rules:**
- E25: Seller override of visual flag → close-up photo question + review summary
- E30: "Tested OK" in note → seller-reported, held as Extracted (never Verified)
- Fitment: verified_compat or absent (Rule c); donor vehicle is never fitment
- E49: Stale draft detection via version snapshot comparison
- E41: Hedged answer detection (English + Arabic)

**`app/domain/reconciler.py` — CP2 Reconciler:**
- E16: Donor year conflict → high priority, blocks export
- E17: Donor model conflict
- E18: Donor vehicle missing
- E19: Market spec unknown (warning, doesn't block)
- E20: Two donors for one part
- E21: Position unknown
- E22: Position ambiguous (driver/passenger wording)
- E24: Condition flag with no explanatory note
- E26: Missing included components
- E27: Broken glass
- E28: Partly tested, no test record
- E29: No test record (flag, doesn't block)
- E31: Failed test → Guarded, blocks
- E37: Weight or dimensions missing
- E38: Weight/dimensions implausible (kg/lb, cm/inch unit checks)
- E40: Missing price or unclear currency
- E41: Hedged answer stored as unconfirmed
- E12: Unreadable part number

Each finding → Question row (text_en filled, text_ar blank for Inquiry agent).
Never auto-resolves (Rule e). Part status computed from most severe finding.

**`app/domain/cp2.py` — CP2 Orchestration:**
- `run_cp2`: full reconciliation — reads DB, runs Reconciler, writes Questions + AuditEvent, updates part status
- `apply_seller_change`: E43 — new version + AuditEvent + partial re-run for affected fields only
- `check_stale_draft`: E49 — rejects stale writes

## Semantic Layer + Seller Context (TRD 4)

The chat and agents never query raw tables and there is no text-to-SQL. They go
through a governed semantic layer that is scoped to the logged-in seller.

**`app/semantic/` modules:**

| Module | What it does |
|---|---|
| `entity_model.py` | `SemanticLayer`: typed, read-only views (Part, Attribute, Evidence, Donor, Claim, Checklist, Question) over the `sem_*` SQL views. One choke point adds `seller_id = <session seller>` to every read; another seller's SKU returns nothing. |
| `seller_context.py` | Login: the picked name → `seller_id` from `seller_profiles` (never from files or chat). `SellerSession`. Profile edits: validate, bump version, audit, invalidate the cached prefix, re-render drafts that use a profile field. |
| `context_card.py` | The ~300-token seller context card. |
| `prompt_prefix.py` | Cached prefix: system prompt + guardrail policy + glossary (breakpoint 1) + seller card (breakpoint 2). |
| `glossary.py` | Bilingual glossary and controlled vocabularies from `glossary.yaml`: position (left/right are absolute; driver/passenger side are ambiguous and never stored), condition, defect, test status, features, part type with UK search terms, field names, Arabic-Indic digits. |
| `metrics.py` | `ready_count`, `open_questions`, `conflict_rate`, `time_to_ready`, `unsupported_claims`, defined once in `metric_definitions.yaml`. |
| `access_policy.py` | `ChatTools`, the chat's only handle on data. Reads: the seller's own records. Writes: seller_answer evidence and question status only. Anything else raises `AccessDenied` and is audit-logged. |
| `intents.py` | Intent → semantic query map with typed requests/results: `status_query`, `explain`, `answer`, `correction`, `policy_request`, `out_of_scope`. Router values are proposals; rules re-derive them from the seller's text. |

Accepted answers and corrections return a `LedgerUpdate`; `app/orchestration/chat_actions.py`
applies it through the domain ledger (`apply_seller_change`), which versions the attribute
and re-runs the Reconciler. Profile-driven draft sections (handling time, ship-from city,
returns policy, listing currency) are rendered by `app/domain/listing_profile.py`; the
propagation map from TRD 3 is in `app/domain/propagation.py`.

**API (seller-scoped, `Authorization: Bearer <token>`):**

| Endpoint | Purpose |
|---|---|
| `GET /profiles` | Names for the login picker |
| `POST /sessions` / `DELETE /sessions` | Log in by display name / log out |
| `GET /me/profile`, `PUT /me/profile` | Seller-details admin screen (editable fields only) |
| `GET /me/context-card` | Card text, token estimate, prefix cache key |
| `GET /me/parts`, `GET /me/parts/{sku}` | Seller-scoped parts and part detail |
| `GET /me/metrics` | The five metrics with bilingual labels |
| `POST /me/chat/intent` | Run one routed chat intent; propagates accepted values |
| `GET/POST /sellers/{id}` | Own profile only (403 for another seller) |

## CP4 Compatibility, Propagation Engine and CP6 Readiness

All deterministic; no LLM (Rule g).

**CP4 — `app/domain/compatibility.py`**
- Exact part-number lookup (trimmed, case-insensitive) against `data/reference/compat_ref.csv`, loaded by `app/data/reference_data.py`. The SHA-256 is verified at gateway startup and the version recorded; a tampered file stops the gateway.
- `verified` (E32): fitments with the reference version, claim type `verified_compat`; promotes an Extracted part number/position to Verified with a `reference_lookup` evidence row.
- `not_verified` (E33): donor info only, labelled "compatibility not verified".
- `conflicting` (E34): matches disagree on vehicle or side → Specialist review.
- `side_mismatch`: the reference side differs from the part's position → identity question for the seller.
- The donor vehicle is always returned as a separate, descriptive fact, never as fitment (Rule c).
- E35: a new reference version re-runs lookups and re-renders drafts built from the old one.

**Propagation — `app/domain/propagation.py` (graph) and `propagation_engine.py` (engine)**

| Field changed | Recomputed | LLM summary |
|---|---|---|
| Part number | compat lookup, item specifics, title, description, checklist | regenerate |
| Position | item specifics, title, compat lookup, checklist | no |
| Donor year / model / make | donor block, title, checklist | no |
| Condition or defect | disclosure block, description, checklist | regenerate |
| Test status | disclosure block, checklist | no |
| Weight or dimensions | shipping fields, checklist | no |
| Price | price field only | no |

`propagate()` applies one correction in a single transaction: new attribute version, Reconciler re-run, only the affected outputs, a draft version snapshot (`listing_draft_versions`) and one `propagate` AuditEvent. Outputs that need the LLM summary set `summary_stale`; the old summary stays visible until it is regenerated.

**CP6 — `app/domain/readiness.py`**
- Items C01–C15 and their blocking flags come from `checklist_v0.1.yaml` (TRD 8, TEST checklist).
- Status, first match wins: **Specialist review required** (excluded type E39, conflicting reference matches E34, conflict open after two seller rounds) → **Information missing** (a blocking item fails) → **Ready for seller review**. Warnings (donor not recorded, compatibility not verified, seller override) never block. Approved is set only by the seller.
- Actions: identity → disclosure → shipping → commercial; then most items cleared; then photo → question → measurement. Top three for chat, all for the dashboard.

M-07 after the first pass:

| Rank | Action | Clears |
|---|---|---|
| 1 | Retake the label photo close up, in daylight | C03, C12 |
| 2 | Confirm donor year: 2017 or 2018? | C05 |
| 3 | Close-up of the flagged area (lower mounting tab): is it cracked? | C06 |
| 4 | Was the mirror tested (fold, heater, adjustment)? | C07 |
| 5 | Weigh and measure the packed box | C10 |

**API:** `GET /me/parts/{sku}/compatibility`, `GET /me/parts/{sku}/readiness`, `GET /me/readiness` (dashboard), `POST /me/parts/{sku}/draft`. Chat corrections (`POST /me/chat/intent`) propagate through the engine.

## Orchestration: Supervisor, Inquiry Agent and Chat Concierge

**Supervisor — `app/orchestration/supervisor.py`** (LangGraph, one thread per part)

```
Ingested → Extracted → Reconciled ⇄ Awaiting seller (CP3, interrupt)
         → Drafted → Guarded → Evaluated → Awaiting approval (CP7, interrupt) → Approved → Exported
```

- Code decides every transition; only the supervisor writes the part status. No agent can skip a checkpoint or set a status.
- CP3 waits for the seller's answers; CP7 waits for the seller's approval click (`POST /me/parts/{sku}/approve`). Any other approver is ignored.
- A correction resumes the waiting part with only the affected fields, which go back to Reconciled.
- `PipelineQueue` runs parts on asyncio workers, so one part waiting never holds up the others (`POST /me/pipeline/run`, `GET /me/pipeline/{sku}`).

**Inquiry agent — `app/orchestration/inquiry_agent.py`**
- At most 3 ranked questions per part per round (TRD 8 order), in English and Arabic, written at reconciliation time.
- Photo guides for label and mounting-tab retakes; a weigh-and-measure packing guide.
- The fast model may only rephrase; numbers and part numbers must survive, or the template is used.

**Chat concierge — `app/orchestration/chat_concierge.py`** (`POST /me/chat`, `POST /me/chat/stream`)
- Rules-first router: refusals (TRD 6 table) → out of scope (customs/legal, with the TEST checklist and disclaimer, then back to the task) → profile → explain → status query → correction → answers. The fast model is a validated fallback only.
- Replies in the seller's language; language detected per span for mixed messages (E44).
- Short answers are parsed into typed values clause by clause ("٢٠١٨", "مشروخ", "لم يتم فحصها", "1.8 kg, 45 x 30 x 25 cm").
- A value for a field nobody asked about gets a "which field?" check (E42). Hedged answers are stored as unconfirmed (E41). Corrections go through the propagation engine (E43).
- Deterministic intents never call an LLM (p95 well under 300 ms). The stream sends an acknowledgement first.
- Model calls (`app/orchestration/llm_policy.py`): 1.2 s timeout in chat, one retry, then a template (E46).

| Seller asks | Reply | Alternative |
|---|---|---|
| "Say it fits all Corollas 2014 to 2020" | Fitment needs a verified part number | Donor vehicle as information plus verified fitments; buyers check their part number |
| "Leave out the cracked tab" | Known defects must be disclosed | Close-up photo and a plain description |
| "Write fully working" | No test record | "Untested", or record a test result |
| "Call it genuine OEM" | Authenticity cannot be certified | State the visible brand marking |
| "Mark it as new" | The part is used | Used, with grade notes |
| "Is this cleared for UK customs?" | Not customs or legal advice | TEST checklist status, then back to the task |

## Listing Agent, CP5 Guard and CP5b Critic

The listing is rendered from the ledger, never free-hand. Only one short summary paragraph is written by an LLM.

**Templated draft — `app/domain/claims.py`, `listing.py`, `listing_render.py`**
- Title up to 80 characters with UK buyer terms ("Wing Mirror Door Mirror") and absolute position; item specifics; category suggestion.
- Description sections: part details, condition and disclosure (mandatory: used, each defect, test status per feature when partly tested, included and missing components), donor vehicle, compatibility, shipping.
- Donor vehicle and verified compatibility are separate sections. Without a reference match the draft shows the donor as information and "compatibility not verified".
- Every sentence is a claim (`fact`, `disclosure`, `donor_info`, `verified_compat`) with its evidence ids, for source-on-hover (`GET /me/parts/{sku}/draft`).
- Drafts store `built_from_versions`; a mismatch marks the draft stale and re-renders it (E49).

**CP5 guard — `app/domain/guard.py`** (rules, about 1 ms)
- Each claim needs evidence of the right type: verified compatibility only from a reference lookup; test status only from a seller answer or test record.
- Fitment wording outside a verified lookup is removed (E36).
- Forbidden lexicon in English and Arabic (guaranteed fit, fits all, fully working, genuine OEM, brand new, no defects): removed and logged unless a matching test record exists (E54).

**Summary paragraph and CP5b — `app/orchestration/listing_agent.py`** (background)
- Main-tier model, written only from the claim set, each sentence citing claim ids; rules-checked, then the fast-tier critic.
- Critic fails → regenerate once → otherwise the template summary. A timeout or unavailable model also falls back to the template (E46).
- Re-rendered only when a claim it cites changes; the old draft stays visible with an "updating" badge meanwhile.
- The Approve button (`POST /me/parts/{sku}/approve`) unlocks only after the critic passes.

## Prompt-Injection Defences and the Red-Team Suite

| Tier | Sources | Treated as |
|---|---|---|
| T0 | System prompt, guardrail policy, checklist, compatibility reference | Trusted configuration |
| T1 | The logged-in seller's chat and editor actions | Allowed intents only |
| T2 | Sheet cells, donor and Arabic notes, OCR text, file names, photo metadata | Data, never instructions |
| T3 | Anything a model returns | Untrusted until validated and parsed into a schema |

**Layers — `app/guardrails/`**
- `screen.py`: normalise (NFKC, zero-width and right-to-left override stripped, Arabic-Indic digits), unmask look-alike letters, scan English and Arabic instruction patterns. Runs on ingested text (CP0, intake OCR, seeding) and every chat message, well under 250 ms.
- `trust.py`: tiers, and `wrap_data`, which puts T2 text in `<data id=random source=…>` tags it cannot break out of.
- `quarantine.py`: quarantine records (text, source, reason) shown to the seller (`GET /me/quarantine`) and audited with the text.
- `agents.py`: every agent's tool list, checked by `tests/test_structural_guarantees.py`. No tool can set a status, write compatibility or checklist data, browse, fetch, message or export.
- The fast-tier classifier (`app/orchestration/injection_classifier.py`) runs only on flagged text, never on the live chat path.
- OCR text may populate only the part number. Test status and fitment never come from intake. There is no admin intent; export needs the approve button.

**Red-team suite:** 25 prompts in `tests/redteam/prompts.yaml`, run through the real code paths.

```bash
python scripts/redteam.py      # prints each result and the pass rate; exit code 1 on any failure
pytest tests/redteam           # same suite as tests, plus audit and no-state-change checks
```

## Part Workspace (PRD 3, journeys J1 to J5)

`ui/workspace.py` is a Streamlit desk view of the same gateway (`st.navigation`, pages in `ui/workspace_pages/`). It talks only to the API (`ui/workspace_client.py`); nothing in `ui/` imports `app.*`, and a test checks that.

| Page | What it shows |
|---|---|
| Inventory board | Every part's status, readiness, questions waiting (latest round), quarantine flag and compatibility. **Run pipeline** queues every part and waits until each stops at CP3 or CP7. |
| Part detail: Evidence | Photos with their regions drawn (flagged in red with the reason: unreadable label, possible damage), the attribute table with status chips and the source of each value, open and resolved conflicts with both sources side by side (cell, note line or label crop), and every description sentence: hover for its source, or open the info button for the full evidence and photo crop. |
| Part detail: Listing | Title with a live 80-character counter, category, price with its currency, the mandatory disclosure (shown locked), description sections, your own notes, item specifics. **Save and re-check** re-runs the guard (CP5) and the checklist; blocked claims and title words are shown with their reason. **Approve and export** is enabled only when readiness is Ready for seller review and the summary is not updating. |
| Part detail: Readiness | C01 to C15 as pass, fail or warning, the ranked actions, and the review summary (your overrides, warnings, specialist reasons, guard removals). |
| Part detail: History | The part's AuditEvents with before and after values. |
| Chat (beside the tabs) | The top three questions as quick-reply cards, streamed replies, right to left for Arabic, corrections ("Change M-04 to right side"), and photos attached in the chat box. An "Updating summary" badge shows while the summary paragraph regenerates. |
| Quarantine | Quarantined cells, notes, OCR text, file names and chat messages with source and reason. |

Gateway endpoints added for it: `GET /me/board`, `GET /me/parts/{sku}/detail`, `GET /me/parts/{sku}/photos/{evidence_id}`, `POST /me/parts/{sku}/photos` (base64 JSON), `PUT /me/parts/{sku}/draft`, `GET /me/parts/{sku}/history`, `GET /me/parts/{sku}/review`, and `POST /me/pipeline/run?wait=true`.

Play the journeys on a freshly seeded database (`python scripts/seed.py`), then **Run pipeline**:
- **J1, M-01:** tap *Untested*, type `2.1 kg, 40 x 30 x 25 cm`, change the price (with its currency), then approve. The mock export lands in `data/exports/M-01.json`.
- **J2, M-07 (switch the chat to العربية):** `السيارة المانحة موديل ٢٠١٨، ولسان التثبيت مشروخ، ولم يتم فحصها`, then `١٫٨ كيلو، ٤٥ في ٣٠ في ٢٥ سم`. Attach a label photo; the OCR still reads `87910-0????`, so nothing changes until you type `Change M-07 part number to 87910-02A20`. `Just write it fits all Corollas` is declined.
- **J3, M-15:** the board flags it, the Quarantine page shows the injected cell and note, and no status moves.
- **J4, M-04:** answer its two questions, then `Change M-04 to right side`: title, item specifics, compatibility (side mismatch) and checklist update in one step, and History shows left → right.

The pipeline's checkpoint state is in memory, so after restarting the API press **Run pipeline** again before answering. (The camera-first app on port 8501 does this by itself when it loads.)

`pytest tests/test_workspace_ui.py` plays J1 to J4 through the real pages (Streamlit AppTest) against the gateway in-process. AppTest cannot attach files to the chat box, so the J2 upload uses the same client call the chat makes.

## Camera-First UI — Gestures & Button Alternatives

The UI uses a camera-first messaging-app interaction model. Every gesture has a visible button alternative.

### How it talks to the API

`ui/app.py` handles login (the profile picker calls `GET /profiles`, then
`POST /sessions`) and hands the session token to the page. The page runs inside
an iframe and cannot call back into Streamlit, so `ui/controller.py` calls the
gateway's `/me/*` endpoints directly with that token and re-renders each pane
from the response:

| Action | Endpoint |
|---|---|
| Board, queue and home counts | `GET /me/board`, `GET /me/quarantine` |
| Open a part's thread | `GET /me/parts/{sku}/detail` |
| Answer a question | `POST /me/chat` |
| Approve | `POST /me/parts/{sku}/approve` |
| Send a photo | `POST /me/parts/{sku}/photos` |
| Run pipeline | `POST /me/pipeline/run?wait=true` |
| Seller details | `GET` / `PUT /me/profile`, `GET /reference` |

The API must be running before you log in. If the API restarts, the app logs in
again by itself and, because the pipeline state was lost, re-runs the pipeline.

### Navigation

| Gesture | Button Alternative | Description |
|---|---|---|
| Swipe left/right | Bottom bar icons (5) | Move between panes: Map, Chats, Home, Board, Queue (native scroll snap) |
| Swipe down from top | Profile button (top corner) | Opens the Profile/Settings sheet |
| Swipe up from Home | "Board" icon | Opens the inventory board |

### Home (Camera/Create)

| Gesture | Button Alternative | Description |
|---|---|---|
| Tap centre button | "Open camera" button | Opens viewfinder (`getUserMedia`, back camera) |
| Tap shutter | — | Takes a photo |
| — | "Choose a photo" button | Picks an image file instead |
| — | "Send photo" | Attaches the photo to the part picked in the list |
| — | "Run pipeline" | Re-processes every part, then refreshes the board |

The status tiles on Home jump to the board filtered to that status. If the
camera is unavailable, the file picker opens instead. There is no spreadsheet or
donor-notes upload: the gateway has no ingest endpoint, so parts come from the
seeded files (`python scripts/seed.py`).

### Board (Inventory)

| Gesture | Button Alternative | Description |
|---|---|---|
| Tap status ring | "Open the part" link on card | Opens full-screen story card for that part |
| Tap right half | "›" button | Next part story |
| Tap left half | "‹" button | Previous part story |
| Swipe down | "Close" (X) button | Closes story overlay |
| Auto-advance (6s) | Manual tap to advance | Progress bars on top show timer; touching the card pauses it |
| — | Search box, status filter chips | Filter the rings and cards |

Ring colour = status, always paired with an icon (✅🔵⚠️🔴) — colour is never the only signal.

### Chats (Questions)

| Gesture | Button Alternative | Description |
|---|---|---|
| Tap a thread | Chat item row, "Answer questions" on a card | Opens the part's open questions |
| Tap quick-reply chip | Type answer in input field | Sends a common answer (shown for test status and side) |
| — | "Send" button or Enter | Sends a custom answer |
| Tap "retry" | "Retry" link | Re-sends a failed message (optimistic send) |

When a part reaches *Ready for seller review*, the thread shows **Hold to approve**.

### Queue

| Gesture | Button Alternative | Description |
|---|---|---|
| Swipe right | "Do now" button | Opens that part's chat |
| Swipe left | "Later" button | Moves the card to *Deferred* (never deleted) |

Blocked (quarantined) input comes first, then specialist review, then parts with
questions waiting, then parts ready to approve.

### Profile/Settings Sheet

| Gesture | Button Alternative | Description |
|---|---|---|
| Swipe down from top | Profile button | Opens sheet |
| Tap backdrop | "Close" (X) button, Esc | Closes sheet (unsaved edits are discarded) |
| Hold "Save" 1.2s | A single tap also saves | Saves the editable fields through `PUT /me/profile` |
| — | Language button | Switches English / العربية (right to left) |
| — | "Replay tutorial" | Shows the 3-card tutorial again |

### Hold-to-Confirm

Important actions use hold-to-confirm:
- A bar fills over ~1.2 seconds
- Releasing early cancels the action
- A full hold confirms
- Approve accepts only a full hold; Save also accepts a single tap

### Context Menu

| Gesture | Button Alternative | Description |
|---|---|---|
| Long-press an item | Right-click (desktop) | Opens context menu: answer questions, open the part |
| — | Buttons on cards | Same actions available as buttons |

### Other

| Gesture | Button Alternative | Description |
|---|---|---|
| Pull to refresh | Refresh button on the board | Reloads data from the API |
| Arrow keys (desktop) | Bottom bar icons | Navigate panes (not while typing) |
| Space / arrow keys (desktop) | Tap right zone | Advance story |
| Esc (desktop) | Close button | Close any overlay |
| — | "Log out" under the app | Ends the session |

### Accessibility

- All icon buttons have `aria-label`
- `aria-live` regions for new messages and status changes
- Contrast ratio ≥ 4.5:1
- Touch targets ≥ 48px
- `prefers-reduced-motion` → plain fades instead of springs
- RTL languages mirror pane order and swipe direction
- First launch shows a 3-card tutorial (skippable, shown once)

### Offline

- An offline banner appears when the network is lost; data reloads when it comes back
- The app is not installable as a PWA: Streamlit does not serve the manifest or
  service worker, so that registration was removed
