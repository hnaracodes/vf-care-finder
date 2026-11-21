# Thesis and plan

## The question

Where in India can people not reach life-saving care, and where does it only look that way because the data is missing?

## Why this question

The obvious project is a "medical desert" map: count facilities per district and compare with health need. That fails on this dataset. The facilities table is a 10,000-row sample of mostly private, web-visible facilities, and 202 of 755 districts show zero. A count map would mostly show where clinics have websites.

The project turns that flaw into the finding. It separates two kinds of gap:

- A care desert: people really cannot reach the service. Send care.
- A data desert: care exists (often public) but is invisible in the dataset. Send data collectors.

Both are decisions Virtue Foundation can act on, and the second one improves the dataset itself.

## The method in four moves

1. Grade what facilities can do, with evidence. A specialty tag is a claim. A named procedure or piece of equipment is evidence. Tag every fact, then apply a transparent recipe per service line to get a grade from 0 to 3. (WP3, WP4)
2. Measure access as distance. From each geocoded post office (a stand-in for a settlement), find the straight-line distance to the nearest facility at each grade. Roll up to district. Every district gets a score, including those with zero facilities. (WP5)
3. Compare access with what the government survey says actually happens. If access looks bad but births still happen in institutions, care exists and the data is blind. A model makes that comparison rigorous. (WP6)
4. Simulate decisions. Given a budget of N upgrades, which sites reach the most people who truly lack access? (WP7)

Then show it in an interactive map and record the video. (WP8, WP9)

## Service lines

| Line | Role in the story | Why |
| --- | --- | --- |
| Emergency obstetric care | Flagship. Full treatment. | It is the only line where NFHS has direct utilization outcomes (institutional births, C-section rate, public share), so the care-versus-data split can be tested. |
| Dialysis | Second line. Need versus access. | NFHS has the burden side (high blood sugar, hypertension) but no utilization outcome, so the data-desert test is weaker. |
| ICU with ventilation | Supporting. Claims versus evidence. | Strong evidence gap (2,048 mention an ICU, 380 also mention a ventilator) but no matching NFHS indicator. |

If time runs short, finish obstetrics end to end before touching the other two.

## The two models

Hruday wants real ML in the project. Each model must change the answer, not decorate it.

### Model A: fact tagger (WP3 teacher, WP4 student)

- Input: one fact string, such as "24x7 emergency caesarean facility with blood storage unit".
- Output: zero or more evidence tags from a fixed taxonomy (`csection`, `blood`, `anesthesia`, `icu`, `ventilator`, `dialysis`, and noise tags such as `reputation` and `location`). See `06_capability_recipes.md`.
- Teacher: an LLM labels a sample of distinct facts.
- Student: a small text model trained on the teacher's labels, then run over all 460,132 facts. Start with embeddings plus logistic regression, and fine-tune a small encoder if time allows.
- Evaluation: a hand-labeled gold set. Report per-tag precision, recall, and F1 for three systems side by side: regex, LLM teacher, student.
- Why a student at all: the pitch is that Virtue Foundation can run it across its whole multi-country dataset without an LLM call per fact. Report cost and speed next to accuracy.
- Facility grades come from rules over the tags, not from the model directly, so every grade can be explained by quoting the facts behind it.

### Model B: outcome model (WP6)

- Unit: district (about 700 rows).
- Target: `institutional_birth_5y_pct` first, then the C-section rate.
- Features: development proxies from NFHS (electricity, sanitation, clean fuel, female literacy and schooling, insurance) plus access features from WP5 (distance to graded obstetric care).
- Model: gradient boosting with a ridge regression baseline. Cross-validate grouped by state, because neighboring districts are not independent.
- Use 1, ablation: does adding the access features improve out-of-fold error? If yes, the facility data carries real signal about care. If no, say so plainly; it means the dataset's coverage is too thin to explain outcomes, which is itself a finding.
- Use 2, residuals: a district whose real outcome is far better than its visible access predicts is a data desert candidate. A district that is as bad as predicted, with poor access, is a care desert.
- Small data rules: no neural nets here, no tuning on the test folds, report uncertainty.

Possible third model if there is time (WP10): claim plausibility, predicting whether a facility has a capability from its size, type, and equipment, to flag unlikely claims.

## District quadrants

For each service line, each district gets an access reading and an outcome reading.

| Access in the data | Outcome in NFHS | Label | Action |
| --- | --- | --- | --- |
| Poor | Poor | Care desert | Send care: missions, upgrades |
| Poor | Good | Data desert | Send data collectors; the public system is probably carrying it |
| Good | Poor | Cost or quality barrier | Look at insurance, out-of-pocket cost, private share |
| Good | Good | Served | No action |

Start with simple thresholds (WP6 picks and records them), then replace the outcome reading with Model B's residual. Keep both versions so the video can show what the model changed.

## What "done" looks like

- A map of India drawn from about 151,000 post offices, colored by distance to evidenced emergency obstetric care, with an evidence slider.
- Three ranked lists: districts to send care to, districts to send data collectors to, districts where cost is the barrier.
- A list of upgrade sites from the simulator, with the gain from each.
- Model metrics: fact tagger F1 against hand labels, outcome model error with and without access features.
- A cleaned facilities table with tags and grades that could be handed back to Virtue Foundation.
- A video of about 3 minutes.

## Limits to state openly

- Straight-line distance is not travel time. Roads and terrain make real trips longer.
- The facilities table is a sample, so the true nearest facility may be closer than the one in the data. That bias runs the other way.
- Post offices stand in for settlements. They are not population-weighted unless WP7 adds census population, and that would be an estimate.
- No evidence is not evidence of absence. Grades describe what the web pages say.
- NFHS-5 is from 2019 to 2021; facility pages are newer.
- District matching between the PIN directory and NFHS is imperfect. Report how many districts matched exactly, by fuzzy match, by hand, and not at all.

## Order of work

See `tasks/README.md` for the dependency graph. In short: WP1 and WP2 first and in parallel, WP8 starts immediately against mock data, then WP3, WP5, WP4, WP6, WP7, and WP9 last.
