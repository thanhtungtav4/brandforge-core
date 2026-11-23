# Vision-grade calibration sample #1 — 23 Nov 2026

Approved daily-digest item 3 (23 Nov 2026): give the library-match
threshold (0.35) its first measured evidence. The threshold itself is
NOT changed by this note — evidence only; any change is a separate
decision for Smith.

## Why the daily loop saw "no grades anywhere"

The 6 generated images of the v4 intro article (draft `d-44bf2dc3c8`)
WERE vision-graded at creation — all six records live in the draft at
`images.images[<slot>].vision_check` (content 2 / style 2, checked
2026-11-22T12:40Z, with written notes). The daily loop's step-2 scan
missed them because the grades sit on the draft's *fulfilled-image*
records, not on the plan slots and not in the library; and the image
library itself held **zero entries**, so the library-side grade
distribution the loop went looking for did not exist.

## What was done

The 6 images are now library entries for `mambagiaokhoe` (via
`add_library_image`, the existing path): `source: "ai"`, provenance
tag `generated-v4-intro`, the draft records' own descriptions, and
measured dimensions (featured 1280×720 = 16:9; in-content 1024×768 =
4:3 — both match the plan spec). Entry ids: lib-dd1c19b671,
lib-dcfeddc924, lib-8c4c2d4768, lib-9651e85150, lib-5ab3b23a9f,
lib-8c20f4b397. Visible via `list_library_images("mambagiaokhoe")`.

Vision grades remain recorded where the core's only record path puts
them — the draft (`record_vision_check` is draft-scoped; library
entries have no grade field in their schema). Distribution over the
6: **content {2: 6}, style {2: 6} — grade-0 rate 0/6.** Readable from
draft `d-44bf2dc3c8`'s store record; `image_check` on that draft
returns ok with no issues. No core tool aggregates a grade
distribution across drafts/library — that aggregation gap is why the
loop reported "no data"; closing it (a library-side grade field, or
the loop scanning draft vision_checks) is a separate proposal.

## Fulfilment probe (throwaway draft, deleted after)

A probe draft with the v4 article's own section headings was
fulfilled from the new library: 5/6 slots matched **above 0.35** —
featured 0.667, s1 0.385, s2 0.625, s5 0.500, all the correct images.
One lexical-matching miss: slot s3 took the s4 entry (0.500, "bốn
loại mắm") over its own, leaving s4 at needs_generation. Reading:
the threshold admits the right images for their own slots, but token
overlap can still swap neighbouring slots — the known weakness the
AI-tagging round flagged; the vision layer (not the threshold) is
what catches a wrong-slot pick. s1's 0.385 is the closest call —
with client photos (thinner descriptions) a correct match could land
near or under 0.35, which is the number to watch as real entries
arrive.
