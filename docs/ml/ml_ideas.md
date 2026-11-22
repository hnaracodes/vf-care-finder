# ML ideas beyond Model A and Model B

Status: brainstorm only, nothing built. Nov 21, 2026, written by an agent in the overnight run (git `369e7af`).

The two planned models are the fact tagger (Model A) and the district outcome model (Model B), see `docs/04_thesis_and_plan.md`. This doc proposes eight more directions, ranks them, and marks two as worth doing tonight once the core pipeline (WP1 to WP7, plus a first cut of WP9) is done.

Every number below comes from throwaway probes run on the raw files through `vf.io`, with the v0 regexes from `docs/06_capability_recipes.md` applied to facts plus description, and a crude exact-name district join (584 of 706 NFHS districts). They are feasibility evidence, not results. Whoever builds an idea recomputes its numbers on the silver and gold tables and WP2's crosswalk.

## Act now, before any stretch work

16.2% of facilities (1,544 of 9,537 with coordinates and a known PIN) have coordinates more than 50 km from the post offices of their own PIN. 4.9% are more than 300 km off. Post offices inside one PIN sit a median 2.5 km from their PIN centroid, so the PIN centroid itself is reliable. Among facilities that mention a C-section, 77 of 436 are affected.

Examples: Aravind Eye Hospital has city Hyderabad, PIN 110002 (Central Delhi) and coordinates in Villupuram, Tamil Nadu. Tata Memorial Hospital has city Mumbai and coordinates in Mumbai, but PIN 440003 (Nagpur). Entity resolution appears to merge branches of chains and similarly named hospitals.

Either field can be the wrong one. Wrong coordinates distort WP5 distances: a misplaced C-section facility creates false coverage in one place and a false gap in another. Wrong PINs distort any PIN-based district assignment. The location check in idea 2 takes about an hour and belongs in WP1 or WP5, not in stretch work.

## Ranking

| Rank | Idea | What it changes | Hours | Fits before Sunday | Tonight |
| --- | --- | --- | --- | --- | --- |
| 1 | Spatial and hierarchical data-desert calls with calibrated uncertainty | Which districts are called data deserts, and `p_real_gap` for the simulator and swarm | 2.5 | Yes | **Yes** |
| 2 | Contamination and location-conflict detector | Facility grades and access distances | 3.5 | Yes | **Yes** |
| 3 | Claim plausibility model (WP10 C) | A trust score and a review queue for claims | 2.5 | Yes | No |
| 4 | Visibility model: which facilities make it into the dataset | Gives the data-desert claim a measured mechanism | 2 (5 with an external registry) | Yes | No |
| 5 | Active learning for the fact tagger and gold set | Makes Model A more than a copy of the regex while there is no API key | 3, including about 1 of Hruday labeling | Only if Hruday labels | No |
| 6 | Uncertainty-aware simulator (Monte Carlo over model outputs) | Turns the upgrade list into a list with a stability score | 3 | Sunday morning at the earliest | No |
| 7 | An honest capacity swarm (deferred approach B) | Adds "reachable but overloaded" as a third kind of gap | 7 or more | No | No |
| 8 | Fact landscape: clustering 275,000 distinct facts | An extraction-quality audit; no district decision changes | 2.5 | Yes, low value | No |

### Why these two tonight

- Idea 1 corrects the core finding. In the probe, district residuals are strongly spatial (Moran's I 0.57), and 64% of their variance is between states. If WP6 thresholds raw residuals, the data-desert list will mostly be a list of states. Idea 1 is sklearn-only, runs on WP6 outputs, and changes the numbers the simulator and the swarm consume.
- Idea 2 fixes errors the regex cannot see. Location conflicts move access distances. Aggregator pages put ICUs into dental clinics. Both feed straight into grades and distances. It also suits the Databricks audience, since it repairs their pipeline's output.

Ideas 3 and 4 are close behind. Idea 3 is cheaper if it is built as one of idea 2's signals, and idea 4 makes a strong supporting chart.

---

## 1. Spatial and hierarchical data-desert calls with calibrated uncertainty

**Predicts.** For each district: how much of Model B's residual is a state effect, how much is a spatially smooth regional effect, and how much is district-specific. Also a calibrated `p_real_gap` and an interval around the predicted outcome.

**Inputs.** Model B's out-of-fold predictions and residuals (`gold/district_quadrants.parquet`), `households_surveyed` from `silver/nfhs.parquet`, district centroids (median of each district's post office coordinates), and state.

**Method.**
1. Decompose the residual as r = state effect + regional effect + district noise.
2. State effect: empirical Bayes shrinkage of each state's mean residual toward zero, weighted by its number of districts.
3. Regional effect: smooth the within-state residual over the 6 nearest district centroids. A cleaner alternative is sklearn's `GaussianProcessRegressor` on centroid coordinates with an RBF kernel plus a `WhiteKernel`. The white-noise term estimates district noise and the RBF term the regional field.
4. Calibration: grouped-by-state conformal prediction (CV+) on Model B. A poor-access district is a data-desert candidate only if its outcome is above the upper end of its conformal interval.
5. Set `p_real_gap` to the conformal p-value, with a documented mapping. Describe it as a p-value, not as a posterior probability.
6. Report two levels: "state-level data desert", where a state's public system is invisible, and "district-level data desert", where a district stands out within its state.

**How it changes the answer.** The data-desert list stops being a disguised list of states. `p_real_gap` drives the desert-aware greedy plan (WP7) and the "care likely exists" dots in the swarm, so both move. The video can say "these states run births through a public system this dataset cannot see" and "these districts stand out even within their state". Those are two different calls for data collectors.

**Evidence from the data.**
- In 584 crudely matched districts, `institutional_birth_5y_pct` has Moran's I of 0.63 over 6 nearest neighbours (permutation p < 0.01).
- The grouped-CV residual of a development-only ridge model has Moran's I of 0.57 and an SD of 11.2 points. State means explain 64% of its variance.
- The top third of residuals concentrates in Uttar Pradesh (38 districts), Madhya Pradesh (27), Odisha (18), Rajasthan (18) and Gujarat (17), out of about 195. These are places where institutional births run above what development predicts, plausibly through public programs. WP6's public-share check (step 4) can test that.
- Blending each residual 50/50 with its neighbours' mean moves 37 of 194 districts in or out of the top third. 23 top-third districts have neighbours whose mean residual is negative, which makes them isolated spikes that are worth a second look.
- The probe model had no access features, so the real Model B residual will differ. Recompute.

**Effort.** 2.5 hours on top of WP6.

**Risk.**
- The NFHS file has household and women counts (median 909 households per district) but no births count, so district sampling noise must be estimated, by the GP white-noise term or a stated assumption, not read off.
- Over-smoothing can erase a real local data desert.
- Never feed neighbours' outcomes into Model B as features: that leaks the target. Smooth the residuals after the fact.

**Fits before Sunday.** Yes. **Tonight: yes, first.**

**What you learn.** Spatial autocorrelation, partial pooling and hierarchical models, Gaussian process regression, and conformal prediction.

---

## 2. Contamination and location-conflict detector

**Predicts.**
- Per facility: whether its location fields disagree, and which one is most likely wrong.
- Per fact: the probability that it describes a different entity, or is aggregator boilerplate or an extractor artifact, rather than this facility.

**Inputs.** `silver/facilities.parquet` (coordinates, PIN, city, source domains, name), `silver/facts.parquet`, and the PIN directory (`silver/post_offices.parquet`).

**Method.**
1. Location consensus. Treat four location sources as noisy votes:
   - the coordinates
   - the PIN centroid
   - the city string, matched against PIN-directory office and district names
   - places named in "Located in ..." facts

   Flag a conflict when the coordinates sit more than 50 km from the PIN centroid, and pick the majority location. Output `location_conflict` and a suggested location. WP5 reruns its headline numbers with flagged facilities moved or dropped, as a sensitivity check.
2. Fact contamination with weak supervision. Write labeling functions:
   - the facility is listed on an aggregator domain with a high tag lift (see the evidence below). This is a facility-level signal, because facts do not carry their source URL.
   - the exact fact text appears at 10 or more facilities (boilerplate)
   - the fact names a PIN or district far from the facility
   - the fact names another hospital (fuzzy name mismatch with `rapidfuzz`)
   - extractor meta-statements ("not mentioned in the provided content")
   - negation ("NICU facility not available")
   - low plausibility from idea 3

   Combine them with a label model that learns each function's accuracy from how the functions agree (the data-programming idea behind Snorkel; a weighted majority vote is the fallback). Then train a logistic regression on TF-IDF features plus facility features, so it generalizes beyond the rules. Hand-check about 150 facts to measure precision.
3. Grades: evidence that rests only on facts flagged as contaminated is discounted, for example from supported to claimed. Keep the original grade alongside, so the change is auditable.

**How it changes the answer.** Access distances are suspect for about one facility in six. Grades change where the only evidence is boilerplate or came from a different hospital. `source_confidence` becomes learned instead of hand-weighted. One honest sentence for the video: "N evidence facts appear to describe a different hospital."

**Evidence from the data.**
- Location conflicts: see "Act now" above (16.2% beyond 50 km, 77 of 436 C-section facilities).
- Aggregator lift, among 4,258 clinics and dentists (2.6% mention an ICU overall):
  - with `hexahealth.com` among their sources: 44.6% mention an ICU, vs 2.0% without (n = 56)
  - `policyx.com`: 26.8% vs 2.2% (n = 56)
  - `bajajfinservhealth.in`: 16.7% vs 2.3% (n = 84)

  Two of the five least plausible ICU claims in idea 3's probe, a dental clinic and a skin clinic, have `hexahealth.com` among their sources and carry the bare facts "ICU facility available" and "ICU available".
- Name collisions: 86 facts cite a PIN more than 50 km from the facility, at 67 facilities. Example: "Astha Hospital is a Bajaj Allianz Network Hospital located in Noida, Uttar Pradesh (PIN 201306)", attached to Asha Hospital, Firozabad.
- Boilerplate: 35% of C-section fact rows (691) are exact strings found at 10 or more facilities, such as "caesarean section" at 75. The share is 30% for dialysis and 32% for blood bank. Short shared phrases are not proof of contamination, but they are weak evidence on their own.
- Extractor artifacts: 885 fact rows at 526 facilities are meta-statements such as "No specific procedures listed for Down Town Hospital Blood Bank in the provided content".
- Off-topic pages matter less than expected. Real-estate, finance and utility patterns hit few facts, and most hits are legitimate (floor area in sq ft, EMI payment options). The dangerous contamination is other healthcare entities, not unrelated websites.

**Effort.** 3.5 hours: 1 for location consensus, 2 for the labeling functions, label model and classifier, 0.5 for the hand check.

**Risk.**
- There is no ground truth, so precision depends on the hand check.
- Chains such as Aravind and Tata Memorial are genuinely multi-site, so a "conflict" may be a merged entity rather than a wrong record.
- Aggregator listings can be accurate.
- Flag and discount. Never delete facts.

**Fits before Sunday.** Yes. **Tonight: yes, second.** Do the location check first, because it is cheap and affects WP5.

**What you learn.** Weak supervision and label models, entity-resolution failure modes, and learning with label noise.

---

## 3. Claim plausibility model (WP10 C)

**Predicts.** P(facility has evidence tag t | everything except the facts that produce tag t), for `csection`, `icu`, `ventilator`, `dialysis` and `blood`. Low probability with a claim present means "implausible claim, review". High probability with no claim means "probably capable but undocumented, verify". The second list is a facility-level version of the data desert.

**Inputs.** Facility type, operator, beds, doctors, deduplicated specialties (multi-hot), other evidence tags, number of facts, number of source domains, followers, social count, website.

**Method.** Gradient boosting (`HistGradientBoostingClassifier`) per tag. Use 5-fold out-of-fold predictions, grouped by state or `cluster_id` so near-duplicates do not leak, then isotonic calibration. Rank by surprise (minus log p) and cut by review budget, for example the 100 least plausible claims per tag. Feed the score into idea 2 as one labeling function.

**How it changes the answer.** It adds a trust score next to each grade, and two lists for Virtue Foundation: claims to verify, and likely capabilities to document. It does not change grades on its own (D010 holds). Grades move only through idea 2's combined decision.

**Evidence from the data.** Out-of-fold AUC, adding feature groups in turn:

| Target (prevalence) | Web footprint and fact count | Plus type, operator, beds, doctors, year | Plus specialties | Plus other evidence tags |
| --- | --- | --- | --- | --- |
| `csection` (4.5%) | 0.67 | 0.73 | 0.84 | 0.89 (AP 0.24) |
| `icu` (21.0%) | 0.74 | 0.87 | 0.90 | 0.93 (AP 0.76) |
| `dialysis` (10.5%) | 0.74 | 0.86 | 0.92 | 0.93 |
| `ventilator` (4.4%) | 0.73 | 0.82 | 0.85 | 0.90 |

- 152 of 2,094 ICU-mentioning facilities get an out-of-fold p below 0.1. The lowest are a dental clinic, a skin clinic, a neuro clinic, a maxillofacial centre and a two-doctor clinic.
- 291 facilities with 2 or fewer doctors or 10 or fewer beds mention an ICU, and 77 such facilities mention a C-section.
- A confound worth stating: the number of facts alone predicts a C-section mention at AUC 0.67, and an ICU mention at 0.72. Web signals without fact count reach 0.58 and 0.70. Evidence grades partly measure how much text was scraped, not only what the facility does.

**Effort.** 2.5 hours.

**Risk.**
- Circularity: the model learns "big hospital, so ICU" and penalizes real small specialists. Small maternity nursing homes in India often do perform C-sections, so a low score for one is a question, not an answer.
- Labels are regex tags, so the model inherits regex errors.
- `csection` has only about 450 positives.
- Use the score for a review queue, never for automatic downgrades.

**Fits before Sunday.** Yes. Not tonight unless it is built inside idea 2.

**What you learn.** Out-of-fold scoring, calibration, and the difference between anomaly ranking and classification.

---

## 4. Visibility model: which facilities make it into the dataset

**Predicts.** The expected number of web-visible facilities (all, and obstetric) per district, given urbanicity and development. The observed-to-expected ratio is a visibility index. The partial effect of the public share of births measures how blind the dataset is to public care.

**Inputs.** Post office counts and branch-office share per district (an urbanicity proxy from the PIN directory), NFHS development proxies, `institutional_birth_in_public_facility_5y_pct`, insurance coverage, and facility counts per district from coordinates via WP2.

**Method.** Poisson regression with log(post offices) as an offset (`PoissonRegressor`), or Poisson gradient boosting, with grouped-by-state CV. Report the public-share effect with a bootstrap interval.

Optional, closer to the literature: a facility-level presence-versus-background model, with post offices as background points. This is how ecologists model species seen only where someone looked.

Optional, with external data: public facility counts per district from Rural Health Statistics or the national facility registry would give a true denominator and an actual inclusion rate. That needs a download and another crosswalk, and the external source has to be flagged.

**How it changes the answer.** The data-desert story gets a mechanism measured from the data itself, not just asserted. The visibility index can enter Model B as a covariate, or act as a prior for `p_real_gap` in idea 1. It also gives a "send data collectors" ranking from expected minus observed supply.

**Evidence from the data.** Crude join, 584 districts, 142 of them with zero facilities by PIN:
- Development proxies explain grouped-CV R² 0.49 of log visible facilities per post office, and 0.51 with the public share added.
- Branch-office share dominates (standardized coefficient -0.75, t = -15.8).
- After development controls, one SD more public share of births (16.2 points) goes with about 0.81 times as many visible facilities per post office (t about -4.6).
- Institutional births themselves have no association with visibility (t = +0.4).
- The dataset is 4.7% public, while the median district delivers 65.5% of births in public facilities.

**Effort.** 2 hours internal, about 5 with an external registry.

**Risk.**
- Without an external registry there is no ground truth for true supply.
- Public share may proxy rural poverty beyond the controls.
- These are ecological (district-level) associations.
- The effect is real but modest (R² +0.02). Describe it as an association.

**Fits before Sunday.** Yes. Not tonight: it supports the story more than it changes a decision.

**What you learn.** Count models with offsets, sampling bias, and presence-only data.

---

## 5. Active learning for the fact tagger and gold set

**Predicts.** Per distinct fact, tag probabilities from a student model, and which facts a human should label next.

**Inputs.** `silver/facts.parquet` (275,095 distinct `text_norm`), regex tags, near-miss pools, and Hruday's labels.

**Method.**
- Student: TF-IDF on words and character n-grams, then one-vs-rest logistic regression.
- Selection: pool-based active learning, uncertainty sampling with diversity (k-means on an SVD of the TF-IDF, one pick per cluster), in batches of 50.
- Labeling: a small command-line labeler writes to a separate file, for example `labels/active_facts.csv`. It is never mixed with `labels/gold_facts.csv`, so the gold set stays untouched by training.
- Run 3 or 4 rounds. Optionally add the label model from idea 2 so negation and meta-statements are handled without hand labels.

**How it changes the answer.** D014 blocked the LLM teacher. Without it, WP4's student can only imitate the regex, and the results table compares the regex with a copy of itself. A few hundred actively chosen human labels give the student signal the regex lacks, which makes Model A's comparison real. The same process gives a human-labeled gold set instead of an agent-labeled one.

**Evidence from the data.**
- 23,768 distinct facts match an evidence regex.
- Near-miss pools (facts with a related word that the regex did not tag): `ot` 23,006, `ob_delivery` 7,661, `dialysis` 3,750, `icu` 1,465, `csection` only 17.
- 86 distinct regex-matched facts contain a negation word, and 885 fact rows are extractor meta-statements.
- The flagship C-section tag has almost no recall gap, so its gains would come from precision (boilerplate, negation), which overlaps idea 2.

**Effort.** 3 hours: 2 for an agent, about 1 for Hruday labeling 150 to 200 facts.

**Risk.**
- It needs Hruday at the keyboard tonight.
- Flagship grades may barely move.
- Small label counts give noisy metrics.
- Drop this if an API key arrives: the teacher replaces it.

**Fits before Sunday.** Only if Hruday labels. Not tonight by default.

**What you learn.** Uncertainty sampling, the value of a label, and why a gold set must stay out of training.

---

## 6. Uncertainty-aware simulator

**Predicts.** For each candidate upgrade site, the distribution of its coverage gain, and how often it lands in the top 10 when model outputs are uncertain.

**Inputs.** WP7's precomputed coverage sets, `p_real_gap` with intervals (idea 1), trust scores (ideas 2 and 3), and location conflicts (idea 2).

**Method.**
- Draw 200 possible worlds. In each world:
  - each district's gap is real with probability `p_real_gap`
  - each supported facility is real according to its trust score
  - each conflicted facility sits at either its coordinates or its PIN centroid
- Run greedy maximum coverage on the expected gain across worlds (sample average approximation).
- Report each pick's top-10 frequency, and the expected regret of the naive plan.
- Coverage sets are computed once, so each world only reweights them. That makes the run cheap.

**How it changes the answer.** The upgrade list gains a stability column, for example "7 of 10 picks hold in 90% of worlds". In the swarm, dot opacity can show the probability a dot is stranded. It is the kind of simulation Hruday asked for, built on numbers the pipeline actually has.

**Evidence from the data.** Depends on WP7. Greedy coverage is submodular, so the greedy plan is within 1 - 1/e of optimal for each fixed world. That guarantee is worth one line on the methods card.

**Effort.** 2 hours of pipeline work, plus 1 in the app.

**Risk.** If `p_real_gap` is poorly calibrated, this inherits it. It is harder to explain on screen than a single list.

**Fits before Sunday.** Sunday morning at the earliest, after WP7 and a WP9 first cut. Not tonight.

**What you learn.** Stochastic optimization, submodularity, and propagating uncertainty through a decision.

---

## 7. An honest capacity swarm (deferred approach B)

**Predicts.**
- Per post office: an obstetric supply-to-demand ratio that accounts for competition, since many post offices share one facility.
- In the browser: dots fill a facility up to its capacity, then spill to the next nearest.

**Inputs.**
- Graded facilities and `n_beds`. Beds are present for 223 of about 450 C-section facilities, with a median of 100 and a maximum of 200,000, which is garbage.
- Imputed beds.
- Post offices as demand points, or census population if an external file is added.

**Method.** Enhanced two-step floating catchment area (E2SFCA), the standard method for capacity-aware access in health geography:
1. Each facility's ratio is its capacity divided by the distance-decayed demand around it.
2. Each post office's access is the distance-decayed sum of the ratios of facilities within reach.

E2SFCA is the steady state of a capacity-constrained swarm. Capacity comes from a quantile gradient-boosting bed model and is propagated by Monte Carlo into bands. The swarm's per-tick step would be capacity-constrained assignment (greedy by distance, or min-cost flow) for the median draw.

**How it changes the answer.** It adds a third kind of gap: reachable by distance but overloaded. It also raises the value of upgrades in crowded catchments.

**Evidence from the data.** The bed model reaches only log-bed CV R² 0.36, with a median error factor of 1.9 times. Capacity can only be shown as a wide range.

**Effort.** 7 hours or more: about 3 for the pipeline, 3 for the app, and 1 for checking.

**Risk.** This is why D011 deferred it:
- Beds are not deliveries per year, and the conversion is an assumption.
- Demand in people needs external census data.
- The dataset misses most public facilities, where most births happen. Capacity would look most overloaded exactly in data deserts, which shows the claim the project argues against.

An honest version shows relative ratios only, only where `p_real_gap` is high, and marks every number "est.".

**Fits before Sunday.** No. It suits after submission, or one methods slide on "what capacity would add".

**What you learn.** Floating catchment methods, assignment and min-cost flow, and Monte Carlo uncertainty.

---

## 8. Fact landscape: clustering 275,000 distinct facts

**Predicts.**
- A cluster for each distinct fact.
- A label for each cluster: clinical, reputation, location, staff, photo caption, meta-statement, or boilerplate.
- Candidate tags the taxonomy is missing.

**Inputs.** `silver/facts.parquet`.

**Method.** TF-IDF, then TruncatedSVD (100 dimensions), then MiniBatchKMeans (about 200 clusters). Hand-label the 50 largest clusters from their top terms and 5 examples each. Add a t-SNE matplotlib figure on a 20,000-fact sample. Sentence embeddings would cluster better, but `sentence-transformers` and `torch` are not installed and are heavy to add tonight.

**How it changes the answer.** It gives an extraction-quality headline for Databricks ("X% of extracted capability facts are not about capability"), and noise-tag examples for Model A. It changes no district decision.

**Evidence from the data.**
- Simple noise patterns match 11.3% of fact rows, which is a floor.
- 1,486 distinct facts appear at 20 or more facilities, covering 86,819 fact rows (about 19%).
- 885 rows are extractor meta-statements.

**Effort.** 2.5 hours.

**Risk.** It produces a picture rather than a decision, and the cluster labels need hand work.

**Fits before Sunday.** Yes, but low priority.

**What you learn.** Text representations, clustering at scale, and how to audit an LLM extraction pipeline.

---

## How the ideas connect

- Idea 3 is one signal inside idea 2. Ideas 1, 2 and 3 all feed idea 6.
- Idea 4 can feed idea 1 as a prior for `p_real_gap`.
- Idea 5 shares idea 2's negation and meta-statement rules.
- Idea 7 needs census population and should wait.

## Not recommended

- Neural networks on the roughly 700 district rows.
- Fine-tuning an encoder tonight.
- Any LLM-based step while there is no API key (D014).
- Automatic grade downgrades from any single score.
