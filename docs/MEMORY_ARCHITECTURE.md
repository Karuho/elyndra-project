# Memory architecture

Elyndra uses layered, disk-first memory so conversational continuity does not require keeping an
ever-growing prompt, transcript or model cache in RAM.

## Memory tiers

1. **Working memory** — at most six recent turns of the active chat in process memory.
2. **Structured session memory** — bounded topics, decisions, pending work, outcomes and recent
   context stored in SQLite.
3. **Episodic memory** — dated decisions, problems, corrections and outcomes linked to a chat and
   optional project.
4. **Semantic memory** — stable facts, rules, routines and preferences explicitly accepted by the
   owner.
5. **Cold transcript** — optional gzip-compressed JSONL files for chats that used full retention.

The language model never receives every historical message. Retrieval selects only a bounded current
summary, matching episodic events, matching semantic memories and a small number of document
fragments.

## Storage and RAM behavior

```text
RAM
├── current task
├── active chat identifier
├── at most six recent turns
└── only the retrieved context needed for one response

SQLite on disk
├── chats and structured summaries
├── episodic records
├── reviewed semantic memories
├── pending memory proposals
├── owner corrections
└── archive metadata

Cold files on disk
└── transcripts/YYYY/chat_ID.jsonl.gz
```

SQLite and the operating system may cache a few database pages. That cache is reclaimable and does
not mean Elyndra loaded the complete memory database into its prompt or application objects.

## Structured consolidation

Every successful chat turn is processed with deterministic rules. The consolidation layer extracts
only high-value state:

- topic statements;
- explicit decisions;
- pending work;
- problems requiring resolution;
- successful outcomes;
- stable preference, rule and routine proposals.

The default path does not invoke the language model. This keeps consolidation fast, inspectable and
available even when no model is configured.

## Review boundary

Conversational text does not silently become permanent semantic memory. A detected preference,
routine or rule becomes a proposal with provenance and confidence. The owner may:

- inspect it;
- correct its wording;
- approve it into semantic memory;
- reject it;
- later edit or delete the approved memory.

An explicit `remember` command remains an owner-authorized direct write.

## Episodic memory

Episodic records preserve decisions, pending tasks, problems, outcomes and corrections without
keeping full transcripts in RAM. They remain linked to their source chat and optional project and
can be searched through FTS5.

Editing or deleting an episode rebuilds the structured decisions, pending work and outcomes for the
chat. The original full transcript, when retained, remains separate evidence and is not silently
rewritten.

## Correction records

`/correct ...` stores:

- the user prompt;
- the original assistant response;
- the owner-supplied correction;
- the source chat and timestamp.

Corrections are learning records, not immediate model training. They can later feed a reviewed
classifier or adapter-training dataset.

## Retrieval budget

A generated response may receive at most:

- three semantic memories;
- two episodic records;
- two document fragments;
- approximately 900 characters of the current structured summary;
- a bounded persona block.

The total local context budget is approximately 3,200 characters before conversation history and the
current prompt. Casual conversation bypasses memory retrieval unless it is contextually related.

## Cold transcript flow

A chat must use `full` retention before it can be archived. Elyndra writes a gzip-compressed JSONL
file, calculates SHA-256, sets file mode `0600` and records archive metadata in SQLite. With
`--prune`, complete turns are removed from SQLite only after the file is successfully written and
hashed. Structured summaries and episodic memory remain available.

Compression is implemented. Encryption and key management remain later hardening work.

## Resource principles

- Keep the active turn window bounded.
- Search indexes instead of loading complete histories.
- Pass excerpts, not complete data stores, to the model.
- Store summaries and metadata on disk.
- Never promote inferred personal facts without owner review.
- Keep transcript retention, archive and deletion visible and reversible where possible.
