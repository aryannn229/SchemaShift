# Safe migration mode (Phase 23)

`schemashift migrate ...` runs the PostgreSQL -> MongoDB copy (and Phase 22 incremental scripts) the way you would want to run it on real data.

| Command | What it does |
|---|---|
| `migrate plan` | the collections, source row counts and the ordered steps a run would take |
| `migrate dry-run` | record counts, estimated time (sampled throughput) and storage (BSON size of sampled documents), and the records that would fail the target validators with reasons. **Writes nothing**: validation uses the server's own `$documents` + `$match` on a read-only aggregate, not even collections are created. Exit code 1 when records would fail |
| `migrate run` | creates collections/indexes if missing, copies each root table in primary-key order in batches, checkpoints after every batch. Starts or resumes automatically |
| `migrate resume` | continue from the last checkpoint (error if there is no manifest) |
| `migrate verify` | post-migration consistency checks |
| `migrate status` | the manifest summary |
| `migrate apply script.py` / `rollback script.py` | run or undo an incremental `evolve.py` (Phase 22) with a manifest |

## Guarantees
- **Checkpoints.** Keyset paging (`WHERE pk > cursor ORDER BY pk LIMIT n`; offset paging for tables without a key). A batch is upserted by `_id`
  (repeating it is harmless) and only then is the manifest rewritten atomically (temp file, fsync, rename). A crash at any instruction leaves the
  previous or the next consistent manifest. **Tested with a real kill**: the CLI runs in a child process that calls `os._exit(137)` after N batches
  (fault-injection variable `SCHEMASHIFT_CRASH_AFTER_BATCHES`, `..._AFTER_STEPS` for scripts); the resumed run completes with no duplicates and no
  missing records (`tests/integration/test_safe_migration.py`).
- **Quarantine, not loss.** A document the validator would reject (checked against the collection's real validator) is not written; it is stored whole,
  with the reasons down to the nested field (`orders[3].price does not match ...`), in `_schemashift_quarantine` and summarised in the manifest.
  Embedded children travel with their root document, so one invalid child quarantines the whole root document.
- **Manifest** (`manifest.json` in `--state-dir`): per-table progress and cursor, completed steps, quarantined records, recovery actions taken (resume,
  snapshots, restores), the last check results, the source fingerprint (row counts) taken at the start; a resumed run warns when it changed.
- **Consistency checks** (`verify`): document count (rows = documents + quarantined), key coverage (every primary key has a document unless quarantined),
  reference coverage under REFERENCE (no child points at a missing parent), embedded array elements vs child rows, and a seeded sample of rows rebuilt
  from PostgreSQL and compared field by field with the stored documents.
- **Scripts.** Reversible steps run directly. A step that cannot be undone needs `--i-understand-this-is-lossy`; the affected collection is first
  copied to `_snapshot_<run>_<collection>` and recorded. MANUAL steps are never executed by SchemaShift (the manifest marks them `skipped`).
  `rollback` runs the rollback code of finished reversible steps in reverse order and restores snapshots only with the same flag.

## Limits
The source must not change while a table is being copied (rows inserted into already finished tables are not re-read; the manifest records when counts
changed). Estimates come from the first batches of every table. The consistency check of embedded rows is skipped for collections that have
quarantined parents. Nothing here makes MongoDB match PostgreSQL's concurrent semantics during the copy; use a maintenance window.
