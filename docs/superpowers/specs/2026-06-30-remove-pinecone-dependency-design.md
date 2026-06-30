# Remove Pinecone Dependency Design

## Goal

Remove Pinecone as a backend runtime dependency. The backend must start and run without a Pinecone API key, package dependency, environment variable, index name, or adapter wiring.

`VectorDAL` remains only as a future extension port for vector indexing/search backends. No production business flow should inject, construct, or call a vector DAL after this change.

## Scope

In scope:

- Remove Pinecone runtime dependency from agent registration, agent update, agent deletion, hub agent sync, agent matching, and context memory search.
- Remove Pinecone configuration from settings and `.env.example`.
- Remove Pinecone package dependency and packaging entries.
- Remove Pinecone implementation modules and tests that validate that implementation.
- Rename runtime scoring fields and reason text from vector terminology to relevance terminology.
- Keep `VectorDAL`, `VectorRecord`, and `VectorSearchResult` protocols/DTOs with comments marking them as reserved extension points.
- Upgrade cleanup tests so executable runtime paths cannot reintroduce Pinecone imports, settings, dependency declarations, or package entries.

Out of scope:

- Migrating or deleting existing Mongo documents that contain historical fields such as `indexed_description_hash` or `description_hash`.
- Adding a replacement vector database.
- Building local embedding storage or a new vector index.

## Architecture

The runtime search architecture becomes Mongo-first and local:

- Agent records remain stored in Mongo.
- Agent matching fetches visible active candidates from Mongo and ranks them with deterministic local relevance scoring.
- Context memory search uses Mongo text search only.
- No startup path constructs a vector client.
- No mutation path updates a vector index.

`VectorDAL` stays in `common.protocols.dal_protocols` with an explicit comment:

> Reserved extension port for future vector indexing/search backends. The current backend runtime does not bind this protocol.

The protocol remains available for a future feature branch but is not part of the current dependency graph.

## Agent Registration And Sync

Agent registration writes only to Mongo:

1. Resolve the agent card.
2. Validate provider, URL uniqueness, visibility, and rate limits.
3. Generate the public URL.
4. Upsert the agent document.
5. Return the registered agent.

The registration path no longer embeds the description, upserts vector records, or rolls back Mongo writes because an index update failed.

Agent update, delete, and hub sync follow the same rule:

- Update Mongo state only.
- Do not read or write `indexed_description_hash`.
- Do not call `VectorDAL.upsert`, `VectorDAL.delete`, or `VectorDAL.delete_by_filter`.
- Existing historical hash fields may remain in stored documents but are ignored by business logic.

The API gateway agent viewset should also lose its vector writer dependency. Route dependencies should provide only the repository and any non-vector services still required by the operation.

## Agent Matching

Agent matching remains inside `AgentFacade` but no longer performs vector search.

The matching flow:

1. Return no matches for an empty query or an explicitly empty `filter_ids`.
2. Fetch visible active candidates from Mongo, respecting user visibility and optional `filter_ids`.
3. Exclude agents with open capability issues.
4. Build searchable text from each candidate:
   - `agent_card.name`
   - `agent_card.description`
   - skill names
   - skill descriptions
   - skill tags
   - default input and output modes
5. Tokenize the user query and candidate text with a lightweight deterministic tokenizer.
6. Compute `relevance_score` from token overlap, phrase hits, and skill/tag matches.
7. Compute `capability_score` with the existing attachment/file support logic.
8. Compute `final_score = relevance_weight * relevance_score + capability_weight * capability_score`.
9. Apply existing debate selection and top-match selection rules.

`vector_score` is removed from internal match objects and external match payloads. Callers that currently consume `vector_score` must be updated to consume `relevance_score`.

User-facing and API reason text changes from:

```text
Match score: 0.82 (vector: 0.77, capability: 1.00)
```

to:

```text
Match score: 0.82 (relevance: 0.77, capability: 1.00)
```

This preserves the meaning of final scoring without keeping vector terminology.

## Context Memory Search

Context memory search uses Mongo text search only.

The search flow:

1. If memory search is disabled, return the existing disabled response shape.
2. Run `keyword_search()` against `ContentStorageRepository.text_search()`.
3. Apply existing temporal decay.
4. Apply existing MMR ordering.
5. Hydrate empty snippets from stored turn notes.
6. Return the existing response metadata with `vector_search_used` fixed to `False`.

`vector_search()`, vector upsert during compaction, and vector index deletion are removed from production code paths.

Methods whose only purpose is vector indexing, such as `index_turn_for_search()` and `delete_room_index()`, should be removed from production protocols, adapters, facades, and compaction callbacks rather than converted to no-ops.

## Error Handling

Agent registration and updates no longer fail due to vector index errors. The remaining failure modes are validation, authorization, agent card resolution, duplicate URL detection, and Mongo write/read errors.

Agent matching no longer catches `VectorIndexUnavailableError`; there is no vector backend in the runtime path. Empty candidate sets still return empty match results and existing caller-level failure messages.

Memory search logs Mongo text search failures and returns empty search results as it does today for failed search components. `keyword_search_used` reflects whether Mongo text search completed successfully. `vector_search_used` is always `False` for compatibility.

`VectorIndexUnavailableError` should be removed with the vector runtime path. A future vector backend can introduce a new provider-neutral error when it becomes an active runtime feature again.

## Configuration And Packaging

Remove:

- `pinecone` from `backend/pyproject.toml` dependencies.
- `dal.pinecone` from package lists.
- Pinecone entries from `backend/uv.lock` after refreshing the lockfile.
- `PINECONE_API_KEY` and `PINECONE_INDEX_NAME` from settings and `.env.example`.
- Pinecone index-name validators and config tests.
- Pinecone docs from runtime setup instructions.

Memory search may keep non-vector configuration such as:

- `MEMORY_SEARCH_ENABLED`
- `MEMORY_SEARCH_KEYWORD_WEIGHT`
- `MEMORY_SEARCH_TEMPORAL_DECAY_ENABLED`
- `MEMORY_SEARCH_HALF_LIFE_DAYS`
- `MEMORY_SEARCH_MMR_LAMBDA`
- `MEMORY_SEARCH_MAX_RESULTS`
- `MEMORY_SEARCH_MAX_SNIPPET_CHARS`

`MEMORY_SEARCH_VECTOR_WEIGHT` and `MEMORY_SEARCH_INDEX_NAME` should be removed because the active implementation no longer has a vector component or external index.

Agent matching config should be renamed away from vector terminology. For example:

- `MATCH_VECTOR_WEIGHT` becomes `MATCH_RELEVANCE_WEIGHT`.
- `settings.match_vector_weight` becomes `settings.match_relevance_weight`.

Capability weighting and threshold settings can remain if still used.

## Tests

Update tests to cover the new behavior:

- Settings load without Pinecone env vars or defaults.
- Container creation does not import or instantiate Pinecone.
- Agent registration writes Mongo data without embedding or vector indexing.
- Agent update/delete/hub sync do not call vector DAL methods.
- Agent matching ranks candidates by `relevance_score` and `capability_score`.
- Agent selection service reason text uses `relevance`.
- Existing callers consume `relevance_score`, not `vector_score`.
- Memory search succeeds with Mongo text search only.
- Memory search response sets `vector_search_used` to `False`.
- Compaction no longer schedules vector indexing callbacks.
- API gateway dependencies no longer include a vector index writer.

Remove or rewrite tests that only validate the retired Pinecone adapter:

- Pinecone client mapping tests.
- Multi-index Pinecone cache tests.
- Pinecone settings fallback tests.
- Pinecone package export tests.

## Cleanup Gate

Add or update a repository gate so the runtime cannot reintroduce Pinecone.

The gate should fail on Pinecone references in:

- Production Python modules.
- `pyproject.toml`.
- `.env.example`.
- Docker/runtime setup docs.
- Package lists.
- Executable scripts.

The only allowed `pinecone` strings should be in cleanup-gate test fixtures or scanner self-tests that prove forbidden imports are detected.

Target end state:

- `rg -i pinecone` returns only allowed gate/self-test references.
- No executable path imports `pinecone`, `dal.pinecone`, or `database.pinecone_db`.
- No runtime setting requires or names Pinecone.

## Acceptance Criteria

- Backend starts without `PINECONE_API_KEY`.
- Installing backend dependencies does not install the `pinecone` package.
- Agent registration, update, delete, and hub sync run without vector indexing.
- Agent matching returns deterministic local relevance results.
- No API payload or reason text uses `vector_score`; callers consume `relevance_score`.
- Context memory search works through Mongo text search and reports `vector_search_used: False`.
- `VectorDAL` exists only as a documented future extension port and has no runtime consumers.
- Tests and cleanup gates pass.
