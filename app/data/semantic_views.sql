-- Entity-model views for the semantic layer (TRD 4).
--
-- Every view carries seller_id and sku, so app/semantic/entity_model.py can
-- scope every read to the logged-in seller with a single predicate.
-- The chat and agents read only through these views (via the semantic layer),
-- never through the raw tables. Views are dropped and recreated by init_db
-- so definition changes take effect on existing databases.

DROP VIEW IF EXISTS sem_parts;
CREATE VIEW sem_parts AS
SELECT p.seller_id,
       p.sku,
       p.id           AS part_id,
       p.part_type,
       p.part_number,
       p.side         AS position,
       p.status,
       p.title,
       p.created_at,
       p.updated_at
FROM parts p;

DROP VIEW IF EXISTS sem_attributes;
CREATE VIEW sem_attributes AS
SELECT p.seller_id,
       p.sku,
       a.part_id,
       a.id           AS attribute_id,
       a.name         AS field,
       a.value,
       a.status,
       (SELECT MAX(v.version) FROM attribute_versions v WHERE v.attribute_id = a.id) AS version,
       (SELECT COUNT(*) FROM attribute_evidence ae WHERE ae.attribute_id = a.id)     AS evidence_count,
       a.updated_at
FROM attributes a
JOIN parts p ON p.id = a.part_id;

DROP VIEW IF EXISTS sem_evidence;
CREATE VIEW sem_evidence AS
SELECT p.seller_id,
       p.sku,
       e.part_id,
       e.id           AS evidence_id,
       e.kind,
       e.source_ref   AS pointer,
       e.content      AS raw_text,
       e.confidence,
       e.created_at
FROM evidence e
JOIN parts p ON p.id = e.part_id;

DROP VIEW IF EXISTS sem_attribute_evidence;
CREATE VIEW sem_attribute_evidence AS
SELECT p.seller_id,
       p.sku,
       a.name         AS field,
       ae.attribute_id,
       ae.evidence_id,
       ae.claim_type,
       ae.claim_value
FROM attribute_evidence ae
JOIN attributes a ON a.id = ae.attribute_id
JOIN parts p      ON p.id = a.part_id;

-- Donor vehicle: descriptive metadata only, never fitment (Rule c).
-- Only the last six VIN characters are exposed; the raw donor note is
-- available as evidence, not here.
DROP VIEW IF EXISTS sem_donors;
CREATE VIEW sem_donors AS
SELECT p.seller_id,
       p.sku,
       d.part_id,
       d.id           AS donor_id,
       d.make,
       d.model,
       d.year,
       d.trim,
       d.market       AS market_spec,
       CASE WHEN d.vin IS NULL OR d.vin = '' THEN NULL ELSE substr(d.vin, -6) END AS vin_last6
FROM donor_vehicles d
JOIN parts p ON p.id = d.part_id;

DROP VIEW IF EXISTS sem_claims;
CREATE VIEW sem_claims AS
SELECT p.seller_id,
       p.sku,
       c.part_id,
       c.id           AS claim_id,
       c.claim_type,
       c.attribute_name AS field,
       c.claim_value  AS text,
       c.evidence_id,
       c.evidence_ids_json,
       c.section,
       c.position,
       c.status,
       CASE WHEN (c.evidence_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM evidence e WHERE e.id = c.evidence_id AND e.part_id = c.part_id))
              OR (c.sources_json IS NOT NULL AND c.sources_json != '[]')
            THEN 1 ELSE 0 END AS supported,
       c.created_at
FROM claims c
JOIN parts p ON p.id = c.part_id
WHERE c.status = 'kept';

DROP VIEW IF EXISTS sem_checklist_results;
CREATE VIEW sem_checklist_results AS
SELECT p.seller_id,
       p.sku,
       cr.part_id,
       cr.id          AS result_id,
       cr.listing_id,
       cr.checklist_json,
       cr.passed,
       cr.created_at,
       cr.rowid       AS seq          -- insertion order: evaluations can share a timestamp
FROM checklist_results cr
JOIN parts p ON p.id = cr.part_id;

DROP VIEW IF EXISTS sem_questions;
CREATE VIEW sem_questions AS
SELECT p.seller_id,
       p.sku,
       q.part_id,
       q.id           AS question_id,
       q.attribute_name AS field,
       q.priority,
       q.question_text AS text_en,
       q.text_ar,
       q.edge_case,
       q.status,
       q.answer_text,
       q.answer_evidence_id,
       q.round,
       q.rank,
       q.action_key,
       q.kind,
       q.guide_en,
       q.guide_ar,
       q.created_at,
       q.answered_at
FROM questions q
JOIN parts p ON p.id = q.part_id;

DROP VIEW IF EXISTS sem_listing_drafts;
CREATE VIEW sem_listing_drafts AS
SELECT p.seller_id,
       p.sku,
       ld.part_id,
       ld.id          AS draft_id,
       ld.title,
       ld.status,
       ld.handling_days,
       ld.shipping_block,
       ld.profile_version,
       ld.updated_at
FROM listing_drafts ld
JOIN parts p ON p.id = ld.part_id;
