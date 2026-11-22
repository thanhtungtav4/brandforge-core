# calibration/work — what is a fixture, what is raw evidence

Two kinds of files live here (review L14):

**Test/evaluation fixtures (tracked, read by tests and probes):**
- `pages_setB.json` — the 9-page calibration corpus (page dicts in
  fetch_site shape). Read by `evaluation/probes/cnm_style_probe.py`
  and the H2 brand-draft regeneration.
- `samples.json` — the 5 CNM blog posts the style probe scores.
- `writer_trial.txt` — the writer-trial draft checked against CNM
  facts/style.
- The derived analysis JSONs (`analysis_setB.json`, `site.json`,
  `recheck_after_fixes.json`, …) — outputs of the calibration run,
  kept as the record the report cites.

**Raw scrape evidence (LOCAL ONLY, gitignored, read by no test):**
- `homepage_raw.html`, `bigscript.txt`, `body_clean.txt` — raw
  captures from caonguyenmoc.vn taken during calibration. They were
  untracked in the review-fix round (M16): they stay on disk as
  working evidence but are not part of the repository and were never
  pushed to GitHub.
