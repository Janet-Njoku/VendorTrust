# Dependency Notes

Decisions about non-obvious dependencies. Revisit when upstream changes.

## `httpx2` (API, dev-only)

- **Why:** `fastapi.testclient` re-exports `starlette.testclient`. Starlette 1.10 imports
  `httpx2` first and documents it as the TestClient requirement. Falling back to `httpx` emits
  `StarletteDeprecationWarning` on every test run.
- **Provenance (checked 2026-10-27):** PyPI `httpx2` 2.16.0, author listed as the original
  `httpx` author; source repository `github.com/pydantic/httpx2` (pydantic organization, not a
  fork; PyPI source URL matches). First release 2026-05-11, 22 releases since. The legacy `httpx`
  package last released 0.28.1 on 2024-12-06.
- **Scope:** declared only in the `dev` dependency group. `uv export --no-dev` confirms it is not
  in the production dependency set, and `pip-audit` on that set is clean.
- **Not a warning suppression:** the warning is resolved by installing the package Starlette
  names, not by filtering warnings.
- **Revert path:** if this is not acceptable, replace `httpx2` with `httpx<0.29` in
  `apps/api/pyproject.toml`, run `uv lock`, and accept the deprecation warning. Tests are
  unaffected.

## pnpm install scripts

Dependency lifecycle scripts are denied by default. `unrs-resolver` (transitive, via the ESLint
config) is explicitly set to `false` in `pnpm-workspace.yaml`; lint works without it.

## TypeScript 6.x

`typescript-eslint` 8.72 rejects TypeScript 7, so `typescript` is pinned to `^6`. Remove the
constraint when typescript-eslint supports 7.
