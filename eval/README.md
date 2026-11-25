# Evaluation (AI Specification v1.1 §13)

`run.py --task <task> --model-slot <slot> --prompt-version <v>` runs the real worker code path
against the provider (or recorded responses), writes `reports/{date}-{task}-{version}.md` and
exits non-zero if any §13.3 gate fails. `--offline` runs only the parts CI runs on every PR.

Datasets (A-31) go under `datasets/` via Git LFS. **Synthetic or consenting-volunteer data only;
no real candidate data is ever added here.**

| Folder | Content (§13.1) | Status |
|--------|-----------------|--------|
| `datasets/cv-48` | 48 synthetic CVs, 4 roles, gold profiles and requirement statuses | to be created (S2–S3) |
| `datasets/answers-160` | 160 written answers with gold scores from 2 HR raters | to be created (S5) |
| `datasets/audio-40` | 40 volunteer recordings with reference transcripts | to be recorded (S3) |
| `datasets/plans-20` | 20 plan requests with human ratings | to be created (S4) |
| `datasets/counterfactual` | pairs generated from cv-48 and answers-160 (A-33) | S7 |

Sprint 1 delivers the skeleton only: the gate table and report writer. Metric code is added with
each task (S2–S7).
