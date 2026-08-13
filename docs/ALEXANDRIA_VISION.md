# Alexandria knowledge system — design note

**Alexandria** is the planned owner-managed knowledge library for Elyndra.

The intended hierarchy is:

```text
Alexandria
├── libraries
│   ├── PHP
│   ├── Oracle
│   ├── Linux
│   └── owner-defined collections
├── sources
├── normalized knowledge units
├── canonical facts
├── retrieval indexes
└── optional skills derived from reviewed procedures
```

A library is not a permanent prompt and is not loaded into RAM as a whole. Sources remain on disk,
are normalized into compact units, indexed locally and retrieved only when relevant. Installing a
library must not silently train model weights or grant new permissions.

Planned stages:

1. Owner-created libraries and source folders.
2. Text and source-code ingestion with provenance.
3. Compact machine-oriented summaries linked to original sources.
4. Versioning, conflicts and review of canonical facts.
5. Optional reviewed skill proposals derived from procedural knowledge.
6. Online updating only when explicitly enabled by the owner.

The web interface will eventually provide library browsing, import status, source provenance,
conflict review and deletion controls.
