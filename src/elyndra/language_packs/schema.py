from __future__ import annotations

import sqlite3


def create_pack_schema(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(value)")
        connection.execute("DROP TABLE temp.fts5_probe")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "SQLite FTS5 es obligatorio para construir un pack lingüístico."
        ) from exc
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE pack_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sources(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, version TEXT NOT NULL,
            source_date TEXT NOT NULL, source_url TEXT NOT NULL,
            original_sha256 TEXT NOT NULL, license_id TEXT NOT NULL,
            attribution TEXT NOT NULL, transformation_notes TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE lexemes(
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, external_id TEXT NOT NULL,
            language TEXT NOT NULL, locale TEXT NOT NULL, lemma TEXT NOT NULL,
            normalized_lemma TEXT NOT NULL, part_of_speech TEXT NOT NULL,
            features_json TEXT NOT NULL DEFAULT '{}', register TEXT NOT NULL DEFAULT 'neutral',
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
            UNIQUE(source_id, external_id)
        ) WITHOUT ROWID;
        CREATE TABLE lexeme_forms(
            id TEXT PRIMARY KEY, lexeme_id TEXT NOT NULL, source_id TEXT NOT NULL,
            form TEXT NOT NULL, normalized_form TEXT NOT NULL, form_type TEXT NOT NULL,
            features_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(lexeme_id) REFERENCES lexemes(id) ON DELETE RESTRICT,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT
        ) WITHOUT ROWID;
        CREATE TABLE synsets(
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, external_id TEXT NOT NULL,
            part_of_speech TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
            UNIQUE(source_id, external_id)
        ) WITHOUT ROWID;
        CREATE TABLE senses(
            id TEXT PRIMARY KEY, lexeme_id TEXT NOT NULL, synset_id TEXT,
            source_id TEXT NOT NULL, external_id TEXT NOT NULL, sense_order INTEGER NOT NULL,
            FOREIGN KEY(lexeme_id) REFERENCES lexemes(id) ON DELETE RESTRICT,
            FOREIGN KEY(synset_id) REFERENCES synsets(id) ON DELETE RESTRICT,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
            UNIQUE(source_id, external_id)
        ) WITHOUT ROWID;
        CREATE TABLE synset_members(
            synset_id TEXT NOT NULL, sense_id TEXT NOT NULL,
            PRIMARY KEY(synset_id, sense_id),
            FOREIGN KEY(synset_id) REFERENCES synsets(id) ON DELETE RESTRICT,
            FOREIGN KEY(sense_id) REFERENCES senses(id) ON DELETE RESTRICT
        ) WITHOUT ROWID;
        CREATE TABLE sense_definitions(
            id TEXT PRIMARY KEY, sense_id TEXT NOT NULL, source_id TEXT NOT NULL,
            language TEXT NOT NULL, definition TEXT NOT NULL, display_order INTEGER NOT NULL,
            FOREIGN KEY(sense_id) REFERENCES senses(id) ON DELETE RESTRICT,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT
        ) WITHOUT ROWID;
        CREATE TABLE sense_examples(
            id TEXT PRIMARY KEY, sense_id TEXT NOT NULL, source_id TEXT NOT NULL,
            language TEXT NOT NULL, example TEXT NOT NULL, display_order INTEGER NOT NULL,
            FOREIGN KEY(sense_id) REFERENCES senses(id) ON DELETE RESTRICT,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT
        ) WITHOUT ROWID;
        CREATE TABLE sense_relations(
            source_sense_id TEXT NOT NULL, relation_type TEXT NOT NULL,
            target_sense_id TEXT NOT NULL,
            PRIMARY KEY(source_sense_id, relation_type, target_sense_id)
        ) WITHOUT ROWID;
        CREATE TABLE sense_relation_terms(
            source_sense_id TEXT NOT NULL, relation_type TEXT NOT NULL,
            target_term TEXT NOT NULL, normalized_target_term TEXT NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY(source_sense_id, relation_type, normalized_target_term),
            FOREIGN KEY(source_sense_id) REFERENCES senses(id) ON DELETE RESTRICT,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT
        ) WITHOUT ROWID;
        CREATE TABLE synset_relations(
            source_synset_id TEXT NOT NULL, relation_type TEXT NOT NULL,
            target_synset_id TEXT NOT NULL,
            PRIMARY KEY(source_synset_id, relation_type, target_synset_id)
        ) WITHOUT ROWID;
        CREATE TABLE usage_labels(
            owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, label_type TEXT NOT NULL,
            label TEXT NOT NULL, PRIMARY KEY(owner_type, owner_id, label_type, label)
        ) WITHOUT ROWID;
        CREATE TABLE informal_entries(
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, language TEXT NOT NULL,
            locale TEXT NOT NULL, expression TEXT NOT NULL, normalized_expression TEXT NOT NULL,
            expansion TEXT NOT NULL, register TEXT NOT NULL, category TEXT NOT NULL,
            ambiguity_notes TEXT NOT NULL, confidence REAL NOT NULL
                CHECK(confidence BETWEEN 0 AND 1),
            offensive INTEGER NOT NULL CHECK(offensive IN (0,1)), examples_json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE emoji_annotations(
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, language TEXT NOT NULL,
            emoji_sequence TEXT NOT NULL, short_name TEXT NOT NULL, keywords_json TEXT NOT NULL,
            categories_json TEXT NOT NULL, ambiguity_notes TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE source_records(
            source_id TEXT NOT NULL, external_id TEXT NOT NULL, record_type TEXT NOT NULL,
            canonical_sha256 TEXT NOT NULL, PRIMARY KEY(source_id, external_id, record_type)
        ) WITHOUT ROWID;
        CREATE INDEX idx_lexemes_lookup
        ON lexemes(language, normalized_lemma, part_of_speech, id);
        CREATE INDEX idx_lexemes_source_lemma
        ON lexemes(source_id, normalized_lemma, id);
        CREATE INDEX idx_forms_lookup ON lexeme_forms(normalized_form, lexeme_id, id);
        CREATE INDEX idx_senses_lexeme ON senses(lexeme_id, sense_order, id);
        CREATE INDEX idx_members_sense ON synset_members(sense_id, synset_id);
        CREATE INDEX idx_sense_relations ON sense_relations(source_sense_id, relation_type);
        CREATE INDEX idx_sense_relation_terms
        ON sense_relation_terms(source_sense_id, relation_type, normalized_target_term);
        CREATE INDEX idx_informal_lookup
        ON informal_entries(language, normalized_expression, category, id);
        CREATE INDEX idx_emoji_lookup ON emoji_annotations(language, emoji_sequence, id);
        CREATE VIRTUAL TABLE lexical_terms_fts USING fts5(
            term, kind UNINDEXED, record_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE VIRTUAL TABLE definitions_fts USING fts5(
            definition, sense_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )
