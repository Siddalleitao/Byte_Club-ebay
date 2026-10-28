-- PartProof SQLite schema
-- Single-file DB at data/partproof.db
-- All identifiers are snake_case. Timestamps are ISO-8601 strings (TEXT).

PRAGMA foreign_keys = ON;

-- ── Sellers ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sellers (
    id            TEXT PRIMARY KEY,          -- UUID
    name          TEXT NOT NULL,
    phone         TEXT,
    whatsapp      TEXT,
    location      TEXT,                      -- e.g. "Dubai, UAE"
    seller_type   TEXT,                      -- individual | dealer
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Seller profiles (TRD 4 seller context card) ──────────────
-- Login resolves the selected display_name to seller_id here, never from
-- text in uploaded files or chat. Contact details stay in `sellers`.
CREATE TABLE IF NOT EXISTS seller_profiles (
    seller_id          TEXT PRIMARY KEY REFERENCES sellers(id),
    display_name       TEXT NOT NULL UNIQUE,
    business_name      TEXT NOT NULL,
    city               TEXT NOT NULL,
    languages_json     TEXT NOT NULL DEFAULT '["en"]',   -- JSON array of language codes
    reply_language     TEXT NOT NULL DEFAULT 'match_user',
    corridor           TEXT NOT NULL,                     -- e.g. "UAE-UK"
    inventory_currency TEXT NOT NULL DEFAULT 'AED',
    listing_currency   TEXT NOT NULL DEFAULT 'GBP',
    fx_rate_source     TEXT,
    fx_rate            REAL,                              -- dated test rate: 1 inventory_currency = fx_rate listing_currency (E40)
    fx_rate_date       TEXT,                              -- the date the test rate is for
    handling_days      INTEGER NOT NULL,
    return_policy_ref  TEXT,
    rack_codes_json    TEXT NOT NULL DEFAULT '[]',        -- JSON array of rack ranges
    product_family     TEXT NOT NULL,
    checklist_version  TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1,        -- bumped on every edit (prompt-cache key)
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Parts ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parts (
    id            TEXT PRIMARY KEY,          -- UUID
    seller_id     TEXT NOT NULL REFERENCES sellers(id),
    sku           TEXT,                      -- seller's SKU, e.g. "M-01" (unique per seller)
    title         TEXT,                     -- raw seller-provided title
    part_number   TEXT,                      -- canonicalized part number
    part_type     TEXT NOT NULL,            -- e.g. "side-view mirror"
    side          TEXT,                      -- left | right
    status        TEXT NOT NULL DEFAULT 'Ingested',
    -- part status enum: Ingested, Extracted, Reconciled, Awaiting_seller,
    -- Drafted, Guarded, Evaluated, Awaiting_approval, Approved, Exported
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Donor Vehicles ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS donor_vehicles (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    make          TEXT,
    model         TEXT,
    year          INTEGER,
    vin           TEXT,
    trim          TEXT,
    market        TEXT,                      -- e.g. "GCC"
    note          TEXT,                      -- donor_note, data only
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Attributes ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attributes (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    name          TEXT NOT NULL,            -- e.g. "functionality", "authenticity", "side", "colour"
    value         TEXT,                      -- current reconciled value (NULL if missing)
    status        TEXT NOT NULL DEFAULT 'Missing',
    -- status enum: Seller-confirmed, Verified, Extracted, Conflicting, Missing, Unverified
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Evidence ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS evidence (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    kind          TEXT NOT NULL,
    -- kind enum: photo_region, ocr_text, spreadsheet_cell, donor_note,
    -- arabic_note, seller_answer, reference_lookup, test_record
    source_ref    TEXT,                     -- file path, cell ref, message id, etc.
    content       TEXT,                      -- extracted/recorded text or region description
    confidence    REAL,                      -- 0.0–1.0 (NULL if N/A)
    raw_data      TEXT,                      -- JSON blob of original payload
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Attribute ↔ Evidence link (many-to-many with claim info) ──
CREATE TABLE IF NOT EXISTS attribute_evidence (
    id            TEXT PRIMARY KEY,
    attribute_id  TEXT NOT NULL REFERENCES attributes(id),
    evidence_id   TEXT NOT NULL REFERENCES evidence(id),
    claim_type    TEXT,                      -- fact, disclosure, donor_info, verified_compat
    claim_value   TEXT,                      -- the value this evidence claims for the attribute
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Compatibility References ─────────────────────────────────
CREATE TABLE IF NOT EXISTS compat_refs (
    id            TEXT PRIMARY KEY,
    part_number   TEXT NOT NULL,
    make          TEXT NOT NULL,
    model         TEXT NOT NULL,
    year_start    INTEGER,
    year_end      INTEGER,
    trim          TEXT,
    market        TEXT,
    region        TEXT,                      -- e.g. "UK", "EU"
    source        TEXT,                      -- e.g. "mock_oem_catalogue"
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_compat_part_number ON compat_refs(part_number);

-- ── Questions (seller Q&A) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS questions (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    question_text TEXT NOT NULL,
    text_ar       TEXT,                     -- Arabic translation (left blank for Inquiry agent)
    priority      TEXT NOT NULL DEFAULT 'medium', -- high | medium | low
    edge_case     TEXT,                     -- which E-ID triggered this question
    attribute_name TEXT,                    -- which attribute this question targets
    answer_text   TEXT,                      -- seller's answer (NULL until answered)
    answer_evidence_id TEXT REFERENCES evidence(id), -- seller_answer evidence behind the answer
    round         INTEGER,                   -- Inquiry round in which this question was asked (NULL = not asked)
    rank          INTEGER,                   -- 1..3 within its round (at most 3 per part per round, FR-06)
    action_key    TEXT,                      -- readiness action it asks for, e.g. weigh_and_measure
    kind          TEXT,                      -- photo | question | measurement
    guide_en      TEXT,                      -- photo or packing guide
    guide_ar      TEXT,
    status        TEXT NOT NULL DEFAULT 'open',  -- open | answered | skipped
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at   TEXT
);

-- ── Claims ───────────────────────────────────────────────────
-- One row per description sentence (FR-12): the UI shows its evidence on hover.
CREATE TABLE IF NOT EXISTS claims (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    attribute_name TEXT,
    claim_type    TEXT NOT NULL,            -- fact, disclosure, donor_info, verified_compat
    claim_value   TEXT,                      -- the sentence
    evidence_id   TEXT REFERENCES evidence(id), -- first evidence row (kept for older readers)
    draft_id      TEXT REFERENCES listing_drafts(id),
    claim_key     TEXT,                      -- stable per draft, e.g. condition.test_status
    section       TEXT,                      -- summary | part_details | condition | donor | compatibility | shipping
    position      INTEGER,                   -- order within the description
    evidence_ids_json TEXT,                  -- JSON list of evidence ids behind the sentence
    sources_json  TEXT,                      -- non-evidence sources: seller profile fields, ledger status
    cites_json    TEXT,                      -- summary sentences: the claim ids they are written from
    status        TEXT NOT NULL DEFAULT 'kept', -- kept | removed (CP5) | retired (no longer rendered)
    removed_reason TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT
);

-- ── Listing Drafts ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listing_drafts (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    title         TEXT,
    subtitle      TEXT,
    description   TEXT,
    condition_note TEXT,
    compatibility_json TEXT,                -- JSON array of compat entries
    checklist_json TEXT,                    -- JSON checklist result
    photos_json   TEXT,                      -- JSON array of photo refs
    price         REAL,
    currency      TEXT DEFAULT 'GBP',
    marketplace  TEXT DEFAULT 'eBay UK',
    status        TEXT NOT NULL DEFAULT 'draft', -- draft | guarded | approved | exported
    built_from_versions TEXT,               -- JSON snapshot of attribute version IDs (E49: stale detection)
    handling_days INTEGER,                  -- from the seller profile (TRD 4)
    shipping_block TEXT,                    -- templated shipping section, re-rendered on profile edit
    profile_version INTEGER,                -- seller profile version the profile sections were built from
    version       INTEGER NOT NULL DEFAULT 1,  -- bumped on every re-render (history in listing_draft_versions)
    item_specifics_json TEXT,               -- JSON item specifics (templated)
    donor_block   TEXT,                      -- donor vehicle block (descriptive only, Rule c)
    disclosure_block TEXT,                  -- condition, defects and test status
    shipping_json TEXT,                      -- JSON packed weight and dimensions
    summary       TEXT,                      -- LLM-written summary paragraph (listing agent)
    summary_stale INTEGER NOT NULL DEFAULT 0, -- 1 when the summary paragraph must be regenerated
    ref_version   TEXT,                      -- compatibility reference version the draft was built from (E35)
    category      TEXT,                      -- category suggestion
    summary_status TEXT,                     -- pending | critic_passed | template
    summary_cites_json TEXT,                 -- claim ids the summary paragraph is written from
    stale         INTEGER NOT NULL DEFAULT 0, -- 1 when built_from_versions no longer matches the ledger
    guard_json    TEXT,                      -- last CP5 result
    seller_edits_json TEXT,                  -- the seller's editor changes: title, category, item specifics, notes
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Listing draft history ─────────────────────────────────
CREATE TABLE IF NOT EXISTS listing_draft_versions (
    id            TEXT PRIMARY KEY,
    draft_id      TEXT NOT NULL REFERENCES listing_drafts(id),
    version       INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,             -- the draft outputs at this version
    reason        TEXT,
    actor         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(draft_id, version)
);

-- ── Compatibility results (CP4) ───────────────────────────────
-- Latest exact-match lookup per part. Fitment comes only from here (Rule c).
CREATE TABLE IF NOT EXISTS compat_results (
    part_id       TEXT PRIMARY KEY REFERENCES parts(id),
    part_number   TEXT,
    position      TEXT,
    status        TEXT NOT NULL,             -- verified | not_verified | side_mismatch | conflicting
    reason        TEXT,
    fitments_json TEXT NOT NULL DEFAULT '[]',
    ref_version   TEXT NOT NULL,
    ref_checksum  TEXT NOT NULL,
    checked_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Reference data versions (E35, E50) ────────────────────────
-- One row each time a new version or checksum of a reference file is loaded.
CREATE TABLE IF NOT EXISTS reference_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,             -- compat_ref | checklist
    version       TEXT NOT NULL,
    checksum      TEXT NOT NULL,
    loaded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Checklist Results ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS checklist_results (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    listing_id    TEXT REFERENCES listing_drafts(id),
    checklist_json TEXT NOT NULL,           -- full structured checklist
    passed        INTEGER NOT NULL DEFAULT 0,  -- boolean 0/1
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Attribute Versions (ledger history) ──────────────────────
CREATE TABLE IF NOT EXISTS attribute_versions (
    id            TEXT PRIMARY KEY,          -- UUID
    attribute_id  TEXT NOT NULL REFERENCES attributes(id),
    part_id       TEXT NOT NULL REFERENCES parts(id),
    version       INTEGER NOT NULL,          -- 1-based version number
    value         TEXT,                      -- value at this version
    status        TEXT NOT NULL,            -- AttributeStatus at this version
    source        TEXT,                      -- evidence source pointer
    reason        TEXT,                      -- why this version was created
    actor         TEXT,                      -- system | seller | test_record
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(attribute_id, version)
);
CREATE INDEX IF NOT EXISTS idx_attr_versions_attr ON attribute_versions(attribute_id);
CREATE INDEX IF NOT EXISTS idx_attr_versions_part ON attribute_versions(part_id);

-- ── Quarantine (TRD 6 defence layer 4) ───────────────────────
-- Text that matched an instruction pattern, carried hidden characters or
-- look-alike letters. Shown to the seller; never used as an instruction.
CREATE TABLE IF NOT EXISTS quarantine (
    id            TEXT PRIMARY KEY,
    seller_id     TEXT REFERENCES sellers(id),
    part_id       TEXT REFERENCES parts(id),
    channel       TEXT NOT NULL,             -- ingest | chat
    source        TEXT NOT NULL,             -- e.g. spreadsheet:M-15:notes, note:M-07.txt, file_name:..., chat
    trust_tier    TEXT NOT NULL,             -- T1 (chat) | T2 (ingested)
    text          TEXT NOT NULL,             -- the triggering text, as received
    reason        TEXT NOT NULL,
    pattern_ids_json TEXT NOT NULL DEFAULT '[]',
    flags_json    TEXT NOT NULL DEFAULT '[]',
    classifier_verdict TEXT,                 -- fast-tier label, when it ran: injection | benign | unavailable
    status        TEXT NOT NULL DEFAULT 'open', -- open | reviewed
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quarantine_seller ON quarantine(seller_id);

-- ── Audit Events ─────────────────────────────────────────────
-- Every change: who (actor), what (action, field), from where (source), before and after values, when.
-- Written only through app/data/audit.py.
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id       TEXT,
    seller_id     TEXT,                     -- for events without a part (chat, profile, files)
    event_type    TEXT NOT NULL,            -- e.g. ingest, extract, reconcile, attribute_change, approval, export
    actor         TEXT,                     -- seller | supervisor | cp2 | cp4 | cp5 | an agent
    action        TEXT,                     -- plain words: "status changed", "seller correction", ...
    source        TEXT,                     -- where the change came from: chat, editor, spreadsheet, reference v0.1, ...
    field         TEXT,                     -- the field or output that changed, if one
    before_json   TEXT,                     -- JSON value before the change
    after_json    TEXT,                     -- JSON value after the change
    detail_json   TEXT,                     -- structured detail
    timestamp     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_part ON audit_events(part_id);

-- ── Approvals (CP7, FR-16) ───────────────────────────────────
-- One row per seller approval click. A later change to the listing invalidates it and stores the diff.
CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    part_id       TEXT NOT NULL REFERENCES parts(id),
    draft_id      TEXT NOT NULL REFERENCES listing_drafts(id),
    draft_version INTEGER NOT NULL,         -- the version the seller approved
    actor         TEXT NOT NULL,            -- always seller
    snapshot_json TEXT NOT NULL,            -- the listing sections as approved
    approved_at   TEXT NOT NULL DEFAULT (datetime('now')),
    invalidated_at TEXT,
    invalidated_by TEXT,                    -- actor of the change that invalidated it
    invalidation_reason TEXT,
    diff_json     TEXT                      -- [{section, field, before, after}]
);
CREATE INDEX IF NOT EXISTS idx_approvals_part ON approvals(part_id);

-- ── Observability (TRD 5 latency budget, TRD 9 cost) ─────────
CREATE TABLE IF NOT EXISTS latency_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    class         TEXT NOT NULL,            -- an interaction class from app/observability/latency.py
    ms            REAL NOT NULL,
    seller_id     TEXT,
    part_id       TEXT,
    detail_json   TEXT,
    at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_latency_class ON latency_events(class);

CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id       TEXT,                     -- the part the call was for, when known
    model_tier    TEXT NOT NULL,
    model_name    TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0,  -- USD at the configured per-1K-token rates
    cached        INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER,
    at            TEXT NOT NULL
);

-- Manual-process baseline times typed into the metrics panel (FR-18), per seller.
CREATE TABLE IF NOT EXISTS metric_baselines (
    seller_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    value         REAL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (seller_id, name)
);
