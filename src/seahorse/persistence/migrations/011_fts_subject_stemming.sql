-- 011_fts_subject_stemming.sql — index the FTS5 subject column + English stemming.
-- FTS5 virtual tables cannot be altered, so episode_fts is dropped and recreated
-- with the subject column INDEXED (the short high-signal field — BM25 could not
-- match it before) and the porter tokenizer (English stemming) wrapping
-- unicode61 (accent/case-insensitive). The external-content episode_content
-- table is unchanged; the index is resynced with the 'rebuild' command.
DROP TABLE IF EXISTS episode_fts;

CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
    ep_id UNINDEXED, body_md, title, tags, summary, subject,
    content='episode_content',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);

INSERT INTO episode_fts(episode_fts) VALUES('rebuild');
