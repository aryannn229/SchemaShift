# SchemaShift

SchemaShift is a compiler that migrates a PostgreSQL schema to MongoDB. It parses DDL, lowers it to an
IR of *guarantees* (cascades, uniqueness, atomicity...), checks which guarantees MongoDB can preserve
(SAFE / CHANGED / BROKEN), picks embed vs reference with a cost model, generates code, and verifies
results against real databases.

Status: Phase 0 (scaffold). See `SPEC.md` for the full plan.

## Quickstart (Windows / PowerShell)
```powershell
copy .env.example .env
docker compose up --build
# backend:  http://localhost:8000/api/v1/health
# frontend: http://localhost:5173
```
Dev tasks: `./scripts/dev.ps1 setup`, `./scripts/dev.ps1 test`, `./scripts/dev.ps1 lint`.
