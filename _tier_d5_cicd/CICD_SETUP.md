# CI/CD Setup — FIESTA

Branch: `tier-d5/f3-cicd` (SUB-D5 INFRA, 2026-05-24).

## What this replaces

Manual `flyctl deploy` from a dev laptop. After this lands, every push runs
tests; merging to `main` ships staging; prod requires one human click.

## Three workflows

| File | Trigger | What it does |
|------|---------|--------------|
| `.github/workflows/ci.yml` | Push to any branch + every PR | `uv sync --frozen` → `pytest tests/ --tb=short -x` on Python 3.11 |
| `.github/workflows/deploy-staging.yml` | Push to `main` AND CI passes | `flyctl deploy --config fly.staging.toml --remote-only` |
| `.github/workflows/deploy-prod.yml` | `workflow_dispatch` only (manual) | `flyctl deploy --config fly.toml --app fiesta-mvp --strategy rolling --remote-only` (requires typing `DEPLOY` into the confirm input) |

## One-time CEO actions

### 1. Create the `FLY_API_TOKEN` GitHub secret

```bash
flyctl auth token
# copy the long string it prints
```

Then in GitHub:
`Settings → Secrets and variables → Actions → New repository secret`
- Name: `FLY_API_TOKEN`
- Value: (paste the token)

Without this secret both deploy workflows will fail at the `flyctl deploy`
step with `Error: No access token available`.

### 2. Create `fly.staging.toml` (prerequisite for staging deploys)

The staging workflow references a `fly.staging.toml` that does not exist yet
(intentionally out of scope for this PR — fly config changes are a separate
review surface).

Quick path: `cp fly.toml fly.staging.toml` then change line 4:
```toml
app = "fiesta-mvp-staging"
```
and create the Fly app: `flyctl apps create fiesta-mvp-staging --org personal`.

Until `fly.staging.toml` exists and the staging Fly app is created, the
staging workflow will fail. CI and the manual prod gate work independently
and do NOT depend on it.

## How to deploy prod

1. Go to GitHub → Actions → "Deploy (prod)" workflow
2. Click "Run workflow"
3. Type `DEPLOY` into the confirm input
4. Click the green button

Rolling strategy means machines update one at a time — no downtime if
healthchecks pass.

## Emergency bypass — manual prod deploy from local

`git push --no-verify` does NOT bypass CI (CI runs server-side after the push
lands). To deploy prod without going through GitHub Actions (CI broken,
GitHub down, secret rotated):

```bash
flyctl deploy -a fiesta-mvp --strategy rolling
```

Run from a checkout of the commit you want to ship. Requires local `flyctl
auth login` first.

## How to debug a failed CI run

GitHub → Actions tab → click the red ✗ on the failing run → click the
`pytest` step. The `--tb=short -x` flags mean the first failing test halts
the run and prints a one-line traceback per frame — usually the cause is
obvious in the last 30 lines. To reproduce locally:
`uv sync --frozen && uv run python -m pytest tests/ --tb=short -x`.
