# Evaluation (AI Specification v1.1 §13, A-30, A-31, A-33)

`run.py` runs the **real worker code path** (Processor + task handlers) over the datasets, computes every
§13.3 gate and writes `reports/{date}-{task}-{version}.md`. Release gates count **only on Kimi**.

```
python eval/run.py --task ALL --offline                     # CI: redaction gates (no provider)
python eval/run.py --task CV_PARSE --provider kimi           # one task
python eval/run.py --task ALL --provider kimi --release      # release: every gate must PASS (NOT_RUN fails)
python eval/run.py --task STT --stt-model large-v3-turbo --stt-device cuda --stt-compute float16
python eval/run.py --task ALL --provider local               # smoke on the local llama-server stand-in
```

A gate is **NOT_RUN** (never PASS) while its dataset or human-ratings file is missing.

## Datasets to provide (synthetic or consenting volunteers only; no real candidate data, ever)

The exact file layout and JSON fields are in [`datasets.py`](datasets.py). Summary:

| Folder | What the AI team prepares | Gates it unlocks |
|---|---|---|
| `cv-48/rubrics/*.json` | 4 job rubrics (job + requirements) | CV match |
| `cv-48/cvs/{id}/document.*` + `gold.json` | 48 CVs + gold profile, redaction targets, requirement statuses, `twinOf` for the 6 injection CVs | Redaction, CV parse, CV match, injection, bias |
| `answers-160/{id}.json` | 160 answers split into segments + 2 HR raters' scores per competency (`twinOf` for injection answers) | Assessment, follow-up, injection, bias |
| `answers-160/followup_ratings.csv` | after a run: `answerId,useful` | Follow-up "useful" |
| `audio-40/{id}.webm` + `{id}.json` | 40 recordings + reference transcript, accent group, technical terms | STT |
| `plans-20/{id}.json` + `ratings.csv` | 20 plan requests; after a run: `planId,questionId,relevantAndClear` | Plan |
| `reports/ratings.csv` | `reportId,statements,unsupported` (human check of 20 reports) | Final report |
| `tts/pronunciation.csv` | `questionId,acceptable` (100 technical questions) | TTS |

## Bias suite (A-33) — `bias.py`
Counterfactual variants of each CV (name swaps by gender / region / religion association, institution tier
swap, graduation year ± 15, career gap) and answers (stated name / origin) run through the same pipelines.
Gates: 0 must-have status flips; mean CV fit-score delta ≤ 0.10 (0–1 scale); mean answer score delta ≤ 0.25.

## Human calibration (UAT, §13.4)
3 HR reviewers score 20 mock interviews blind; put their scores in `answers-160` format and run the
ANSWER_ASSESSMENT gates: targets ±1 of the HR median for ≥ 80 % of pairs and QWK ≥ 0.60.
