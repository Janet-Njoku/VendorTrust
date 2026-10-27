# VendorTrust

Third-party security due diligence and continuous control validation platform.

VendorTrust automates vendor onboarding, risk-based security questionnaires, evidence validation,
reviewer-verified control effectiveness, inherent and residual risk scoring, remediation tracking
and audit-ready reporting.

> **Status: Phase 0 (foundation).** There are no business features yet. See
> [docs/roadmap.md](docs/roadmap.md).

Mapping controls to ISO/IEC 27001:2022, SOC 2 or NIST CSF 2.0 is for reference only. It does
not imply certification or compliance.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, workflow states, framework mapping, data model |
| [docs/risk-methodology.md](docs/risk-methodology.md) | Inherent/residual scoring, evidence stages, risk acceptance |
| [docs/security-controls.md](docs/security-controls.md) | Controls for vendor data and uploaded evidence |
| [docs/roadmap.md](docs/roadmap.md) | Phased implementation plan |
| [docs/dependency-notes.md](docs/dependency-notes.md) | Rationale for non-obvious dependencies |

## Layout

```
apps/api/    FastAPI service (Python 3.12, managed with uv)
apps/web/    Next.js app (TypeScript, Tailwind CSS, pnpm)
docs/        Architecture and methodology
docker-compose.yml   Local dev services (ClamAV)
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically from `apps/api/.python-version`)
- Node.js 24 LTS (see `.node-version`) and [pnpm](https://pnpm.io/) 12 (version pinned by the
  `packageManager` field in `package.json`)
- Docker (only for ClamAV, needed from Phase 4)

macOS: `brew install uv pnpm` plus Node 24 (for example `brew install node@24`, or a version
manager that reads `.node-version`).

## Local setup

```bash
# Environment files (placeholders only; real .env files are git-ignored, never commit them)
cp apps/api/.env.example apps/api/.env
cp apps/web/.env.example apps/web/.env.local

# API
cd apps/api
uv sync
uv run uvicorn app.main:app --reload --port 8000
# http://localhost:8000/health  ->  {"status":"ok"}

# Web (new terminal, from the repo root)
pnpm install
pnpm web:dev
# http://localhost:3000
```

The API reads `apps/api/.env` relative to its working directory, so run it from `apps/api`.

No Supabase project is required in Phase 0.

## Checks

```bash
# API (from apps/api)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest

# Web (from the repo root)
pnpm web:lint
pnpm web:typecheck
pnpm web:test
pnpm web:build
```

GitHub Actions runs the same checks (`.github/workflows/`), plus secret scanning and dependency
audits.

## Security notes

- Never commit `.env` files or keys. The Supabase service-role key is server-side only and must
  never use a `NEXT_PUBLIC_` name.
- Evidence will only ever live in a private bucket behind signed URLs and is quarantined until
  malware scanning succeeds. See [docs/security-controls.md](docs/security-controls.md).
- pnpm dependency install scripts are denied by default (`pnpm-workspace.yaml`).
