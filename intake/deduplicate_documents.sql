-- Document archive: one row per document.
--
-- 11,298 attachments, 4,041 actual documents. Same file lands in four
-- mailboxes and every hop re-encodes it, so bytes differ.
--
-- Used to group by filename + day. Wrong both ways:
--   same doc, two days  -> two rows (1,708 extra)
--   two docs, same name, same day -> one row, second one gone (178 of them)
--   invoice.pdf alone was 13 attachments and 5 different documents
--
-- Every attachment already had a content hash. Nobody had used it.

SELECT *
  FROM (
    SELECT DISTINCT ON (a.fingerprint)
           a.id,
           a.name,
           a.kind,                  -- classifier output, NULL ~22% of the time
           a.draft_or_final,
           COALESCE(a.document_date, m.received_at::date) AS happened_on,
           m.received_at,
           m.subject,
           -- When we're the sender the counterparty is the recipient,
           -- otherwise half the archive says "from ourdomain.com".
           CASE WHEN m.sender ILIKE '%@' || :own_domain
                THEN COALESCE(substring(m.recipients FROM '@([A-Za-z0-9._-]+)'), :own_domain)
                ELSE split_part(m.sender, '@', 2)
           END AS counterparty,
           j.reference AS job_ref,
           (a.stored_path IS NOT NULL) AS openable,
           -- window runs before DISTINCT ON, so we keep the count without
           -- keeping the rows. Shows as "5x" in the UI.
           COUNT(*) OVER (PARTITION BY a.fingerprint) AS copies
      FROM attachments a
      JOIN messages m ON m.id = a.message_id
      LEFT JOIN message_files mf ON mf.message_id = m.id
      LEFT JOIN files f ON f.id = mf.file_id
      LEFT JOIN jobs  j ON j.id = f.job_id
     WHERE TRUE
       -- optional filters get appended here, one AND each
     ORDER BY a.fingerprint,
              (a.stored_path IS NOT NULL) DESC,   -- keep a copy we can open
              m.received_at DESC
  ) d
 ORDER BY d.happened_on DESC, d.name
 LIMIT :limit OFFSET :offset;
