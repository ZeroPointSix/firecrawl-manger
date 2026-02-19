# Repository Guidelines

## Project Structure & Module Organization
- `app/`: FastAPI gateway (data plane `/api/*`, control plane `/admin/*`), config (`app/config.py`), middleware, and embedded WebUI build output under `app/ui2/`.
- `webui/`: Vue 3 + Vite + TypeScript source for `/ui2/`.
- `migrations/` + `alembic.ini`: Alembic migrations for the SQLAlchemy models.
- `tests/`: pytest suite (unit/integration plus opt-in E2E).
- `scripts/`: local/devops helpers (bootstrap, run, smoke checks, cleanup).
- `docs/`: architecture + API contract. Treat `docs/agent.md` as the single source of truth for semantics and failure strategy.

## Build, Test, and Development Commands
Backend (Python 3.11):
- Bootstrap venv on Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap-python.ps1`
- Install deps: `pip install -r requirements.txt -r requirements-dev.txt`
- Migrate DB: `alembic upgrade head`
- Run API: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

Frontend:
- Install: `cd webui && npm ci`
- Build (writes to ignored `app/ui2/`): `npm run build`
- Dev server: `npm run dev`

Docker:
- Dev stack: `docker compose up --build`

## Coding Style & Naming Conventions
- Python: use Ruff for lint/format (`ruff check .`, `ruff format .`); line length is 100 and target is `py311` (see `pyproject.toml`).
- Frontend: TypeScript + Vue; run `npm run type-check` before opening a PR.
- Prefer explicit, descriptive names; keep modules cohesive under `app/api/`, `app/core/`, `app/db/`, and `app/observability/`.

## Testing Guidelines
- Run: `pytest --cov=app --cov-fail-under=80` (coverage gate is required).
- E2E tests are marked `e2e` and require `FCAM_E2E=1`; real-upstream calls are additionally gated by `FCAM_E2E_ALLOW_UPSTREAM=1` to avoid accidental quota/cost.

## Commit & Pull Request Guidelines
- Use Conventional Commits (seen in history): `feat:`, `fix:`, `docs:`, `test:`, `chore:`; scopes are OK (e.g., `feat(webui): ...`).
- PRs should include: clear description, linked issue/ticket, relevant tests, and docs updates when behavior/API changes (docs-first). Include screenshots for `/ui2/` UI changes.

## Security & Configuration Tips
- Never commit secrets. Use `.env.example` as a template; inject `FCAM_ADMIN_TOKEN` and `FCAM_MASTER_KEY` via env/Docker secrets.
- Treat `Authorization`, API keys, and tokens as sensitive during review and in logs (mask/redact where applicable).
