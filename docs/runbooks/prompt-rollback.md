# Runbook: prompt rollback (target < 15 minutes)

**When:** a prompt change causes `RepairRateHigh`, policy rejections, failed validations or a quality complaint.

Prompts are versioned files `docs/prompts/{promptId}/v{x.y.z}.md`; the active set is `docs/prompts/active.yaml`,
baked into the ihr-ai-worker image (A-11). Old versions are never deleted, so rollback = revert one line.

## Steps
1. Identify the prompt and version: `run.promptId` / `run.promptVersion` on recent `.completed`/`.failed`
   events (core `ai_runs`), or `ihr_ai_validation_total{result!="valid"}` by task.
2. Revert `active.yaml` to the previous version (one line), e.g. `cv_match: 1.0.1` → `cv_match: 1.0.0`:
   `git revert <commit>` or edit + commit on a hotfix branch.
3. `tools/prompt_lock.py --check` must pass (file exists, hash matches the lock for frozen versions).
4. CI (contracts-check) → build image → deploy ihr-ai-worker (standard and realtime).
5. Verify: new events carry the old `promptVersion` and `promptSha256`; repair rate returns to baseline.

## Rules
- Never edit a released prompt file in place; every change is a new version with a changelog line and an
  evaluation run (§13.3) on Kimi.
- Rollback needs no evaluation run (it restores an evaluated version).
