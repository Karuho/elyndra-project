# Hot, warm and cold memory

Elyndra separates retrieval cost from durability.

## Hot

Hot memory is a bounded process-local cache of the most recent retrieval results. It stores at most 16 query keys and at most 12 results per query. It is discarded on process exit and invalidated when a new chat turn is consolidated. The full database is never copied into RAM.

## Warm

Warm memory searches active recent chat episodes in SQLite. The default window is 30 days. It preserves chat and project provenance and favors the active project without treating recent text as an approved permanent preference.

## Cold

Cold memory consists of owner-approved semantic memories plus a durable provenance index for older episodes. Consolidation indexes eligible episodes without deleting or rewriting their source rows. It does not promote unreviewed preferences, routines or identity claims into approved memory.

## Controls

```bash
./scripts/elyndra-dev memory tiers
./scripts/elyndra-dev memory tier-recall "consulta" --project proyecto
./scripts/elyndra-dev memory consolidate --min-age-days 30 --approve
./scripts/elyndra-dev memory recalls
./scripts/elyndra-dev memory cold-forget ID --approve
```

Recall telemetry contains query SHA-256, tier counts and latency. It does not store the raw query in `memory_recall_events`. Logical forgetting of a cold-index row keeps the source provenance intact and prevents automatic reinsertion through the unique source key.
