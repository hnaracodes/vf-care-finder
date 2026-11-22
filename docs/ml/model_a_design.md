# Model A design: the fact tagger (WP3 labels, WP4 student)

Status: design v2, revised Nov 21, 2026, about 15:45 ET, after a review. Each critique and what changed is in the Review log at the end. Nothing here has been trained.
Inputs read: `docs/04_thesis_and_plan.md`, `docs/06_capability_recipes.md`, `tasks/WP3_teacher_labels.md`, `tasks/WP4_fact_tagger.md`, `docs/07_data_contracts.md`, `docs/ml/model_b_design.md`, `data/silver/*`, `data/gold/fact_tags.parquet` and `data/gold/capability_grades.parquet` (regex v0), and WP3's gold set: `labels/gold_sample.csv`, `labels/gold_facts.csv` (429 texts, written 15:15), `labels/regex_scores.json`, `reports/model_a_results.md`.
Where numbers come from: every number without another source note is printed by `docs/ml/model_a_numbers.py`, a read-only script in the repo (about 1 minute; `--fast` skips the near-duplicate graph). Its output blocks are lettered, and this doc cites them as [A] to [M]. "Reviewer" marks a number measured by the review's scripts and not rerun here. Facility counts use the 9,989 facilities in `gold/capability_grades.parquet`; 9,929 of them have at least one fact.

## Summary

1. Plan for no API key. At 15:04 ET `ANTHROPIC_API_KEY` in `.env` is still empty (D014). Tonight's Model A is a TF-IDF plus logistic regression student trained on two label sources: rules (regex v1 plus a few labeling functions) for every text, and agent labels for the 2,432 strings that decide the flagship grades. Those strings are every C-section and blood candidate, every evidence-tagged fact with a negation cue, and the negation pool [H]. Agent labels live in their own file and are never gold. If a key arrives by about 22:00 ET, the Haiku 5.5 teacher labels the same texts and the same student is retrained on its labels. In both cases the map uses Model A's tags, and the report's first line says which labels trained it.
2. Grade 3 is thin, so precision and a facility-by-facility audit matter more than recall. EmOC grade 3 has 43 facilities. 66% of the 444 facilities at EmOC grade 2 or 3 rest on one distinct C-section text, and 36 strings make up half of all facility-level C-section mentions [C, E]. Five facts about neonatal exchange transfusion would lift 5 facilities to EmOC grade 3 and bring 22,995 of 151,010 post offices more than 10 km closer [F]. Under v1 those facts are not blood evidence. The headline metric is therefore a facility audit: a reader checks the evidence of every EmOC grade-3 facility and of every facility whose EmOC grade changes between methods.
3. The regex is already good at the fact level on the flagship tags. On WP3's gold set (429 agent-labeled texts, weighted by stratum), regex v0 scores precision 0.97 and recall 1.00 on `csection` and 0.96 and 0.95 on `blood` (`labels/regex_scores.json`). The countable errors are elsewhere: neonatal ICU text (77 facilities have ICU evidence only from neonatal mentions [D]), nephrologist bios counted as dialysis units, single facts that satisfy a whole grade-3 rule (125 of 587 dialysis grade-3 facilities [C]), and vocabulary gaps such as "blood centre" (83 texts missed), hyphenated "round-the-clock" (221) and "PM-JAY" (126) [D]. Regex v1 fixes most of these, so every map-level comparison is against regex v1, not v0.
4. Taxonomy v1 is frozen as the working definition (the orchestrator logs it as a default; Hruday can override). It has 21 tags, including the new `negated`. Facts about a named person are `staff_name` only. There is no automatic dialysis implication. A corroborated grade (3) must rest on at least two distinct facts. Where grades are unaffected, v1 follows WP3's gold labeling rules, so gold needs a reconciliation pass of about 30 rows, not a relabel [I].
5. Evaluation, most important first: the facility audit; gold (per tag, weighted by stratum, near-miss rows reported separately); agreement with the labels on a 15% test split built from near-duplicate components; a region test on 4 states never used in training; and changes to the grade sets on the map (counts entering and leaving grade 2+ and grade 3, Jaccard overlap). Thresholds come from cross-fitted out-of-fold scores, never from gold.
6. Freeze rule: the taxonomy, `REGEX_V1` (with its hash), the student and its thresholds are frozen on gold and the audit before WP6 runs. No Model B result is ever used to choose any of them. The access measure that Model A feeds mostly tracks where web-visible private hospitals are: 393 of the 444 EmOC grade 2+ facilities are private and 18 public [L].
7. Off by default tonight: the embedding student, the local Ollama teacher and fine-tuning. Swap was 22.4 of 23.6 GB at 15:20 ET. That time goes to the facility audit.

## 1. What Model A does, in one paragraph

A fact such as "operates a 24x7 blood centre" goes in; zero or more tags such as `blood` and `h24` come out. Rules in `vf.recipes` then turn a facility's tags into a grade per service line, so every grade can be explained by quoting facts (D010). A labeler (rules, a careful agent, or an LLM teacher) labels a designed set of facts, and a small model, the student, learns to copy those labels. With an LLM teacher this is called distillation: you pay for the expensive model once, on a sample, and get a cheap model that runs anywhere. Model A is the student. It answers three questions. Does it copy its labels on texts and regions it never saw? Do the grades it produces survive a person reading each facility's evidence? Does it beat regex v1 on gold?

## 2. What the data says

All counts are distinct normalized facts (`text_norm`) unless they say otherwise.

| Measure | Value |
| --- | --- |
| Fact rows in `silver/facts.parquet` | 451,105 (231,465 capability, 163,645 procedure, 55,995 equipment) [A] |
| Distinct `text_norm` | 275,095; 88.0% appear once [A] |
| Length of a distinct fact | mean 54.8 characters, median 46, 90th percentile 93, max 1,420; median 6 words [A] |
| Facts per facility (the 9,929 with any) | mean 45.4, median 36 [A] |
| Distinct facts with any v0 evidence hit | 23,768; with a hit on a grade-relevant tag (stratum S1) 16,235; with 2 or more tags 4,699 [B] |
| Evidence-word facts with a negation cue | 74 of 18,911 (0.4%) [D] |
| Extractor meta-statements ("not listed in the provided content") | 874, of which 17 have an evidence hit [D] |
| Photo captions ("visible in the image" and similar) | 436 [D] |

Regex v0 hits per tag [B]:

| Tag | Distinct facts | Fact rows | Facilities |
| --- | ---: | ---: | ---: |
| `ob_delivery` | 5,242 | 7,117 | 2,599 |
| `csection` | 364 | 688 | 444 |
| `blood` | 735 | 1,256 | 602 |
| `anesthesia` | 1,530 | 1,760 | 1,141 |
| `ot` | 2,776 | 4,138 | 2,028 |
| `h24` | 4,075 | 9,750 | 4,064 |
| `nicu` | 972 | 1,396 | 707 |
| `dialysis` | 1,757 | 2,904 | 1,036 |
| `dialysis_unit` | 956 | 1,357 | 631 |
| `icu` | 3,067 | 4,665 | 2,052 |
| `ventilator` | 547 | 736 | 435 |
| `emergency` | 6,037 | 10,775 | 3,737 |
| `scheme` | 950 | 1,349 | 774 |

Regex v0 grades over the 9,989 facilities [C]:

| Grade | EmOC | Dialysis | ICU |
| --- | ---: | ---: | ---: |
| 0 none | 5,307 | 7,631 | 7,252 |
| 1 claimed | 4,238 | 1,322 | 685 |
| 2 supported | 401 | 449 | 1,677 |
| 3 corroborated | 43 | 587 | 375 |
| Grade 2 or 3 resting on one distinct text for the defining tag | 294 of 444 (66%) | 401 of 1,036 (39%) | 961 of 2,052 (47%) |
| Grade 3 whose whole rule sits in one distinct text | 0 of 43 | 125 of 587 (21%) | 18 of 375 (5%) |

Known regex errors, by where they come from [D]:

- ICU and neonatal text. 558 distinct `icu` texts contain a neonatal word. The original test ("every `icu` fact of the facility mentions neonat, nicu or newborn") flags 202 facilities at ICU grade 2 or 3. That test is too broad: it also catches "has icu, ccu and nicu with ventilators". After deleting the neonatal phrases and re-testing for an ICU word, 77 facilities remain (67 at grade 2, 10 at grade 3). 34 of the 375 ICU grade-3 facilities have ventilator evidence only from neonatal facts.
- Nephrologist bios counted as dialysis units. The v0 `dialysis_unit` pattern includes "nephrologist". 44 facilities have a `dialysis_unit` hit but no `dialysis` hit, and all 50 of their `dialysis_unit` facts name a nephrologist ("dr. ... is a nephrologist affiliated with ..."). They sit at grade 0 or 1 today. The v1 draft of this doc would have lifted all 44 to grade 3 through the implied-tag rule. 13 of today's 587 dialysis grade-3 facilities rest on nephrologist facts alone.
- Claims dressed as evidence. 33.5% of `ob_delivery` hits and 35.5% of `anesthesia` hits contain department, degree, course, training, specialist or "Dr" wording. Not all are wrong ("has an anaesthesiologist on staff" is evidence). For EmOC grade 3 this matters little: only 5 of the 43 grade-3 facilities reach it through `anesthesia` without `ot`, and none of them relies only on person, department or course facts.
- Negation and meta-statements. 49 fact rows at 45 facilities carry both an evidence hit and a negation or "not listed" cue. The cue words misfire, though: "closed" appears in 6 evidence-tagged texts and none is a negation ("closed reduction", "closed globe trauma"), and "referral" appears in 15, mostly as "referral centre for ...", which is a positive claim.
- Recall, vocabulary. Texts that use the words, and how many of them the tag's v0 regex misses: "blood centre" 98 and 83; hyphenated "round-the-clock" 296 and 221; "PM-JAY" or "PM JAY" 314 and 126; "ventilation" or "BiPAP" 131 and 107; "MICU", "SICU", "PICU", "CCU", "ICCU" 652 and 336; caesarean spelling variants 391 and 27.
- Not a problem: descriptions. Adding description sentences as extra facts leaves EmOC grade 3 at 43 and moves EmOC grade 2+ from 444 to 449 and ICU grade 2+ from 2,052 to 2,088. Model A stays on facts.

Errors cluster by string [E]. `csection` has 364 distinct texts at 665 facility incidences, and 36 strings cover half of those incidences ("caesarean section" alone is at 70 facilities, "cesarean delivery" at 65). For `blood`, 150 strings cover half of 1,171 incidences ("blood bank" at 109 facilities). One wrong call on a common string flips dozens of facilities, which is why section 8 weights precision by facilities and audits facilities directly.

## 3. Tag taxonomy v1 (frozen as the working definition)

Decision: keep the 13 evidence tags, sharpen their definitions, add one modifier tag (`negated`), keep the 7 noise tags. That gives 21 tags. Bump the recipe version to `v1`. WP3 owns `vf.recipes` and `06_capability_recipes.md`; the orchestrator logs the change. The definitions below are also the agent labeler's and the gold reconciler's (Appendix A is the full text, Appendix D lists what changed from WP3's gold rules).

| Tag | v1 definition and decision | Why |
| --- | --- | --- |
| `ob_delivery` | Deliveries, maternity service, labour room or ward. A specialty, department or degree name alone is not. A maternity word in a facility name counts (a name is a claim). | It only lifts a facility to grade 1 (claimed). Following the gold rule on names avoids a relabel. |
| `csection` | Performs caesarean sections. Implies `ob_delivery`. | The flagship "supported" tag. Regex recall is already 1.00 on gold; the work is precision on negations and a facility audit. |
| `blood` | Blood bank, blood centre, blood storage unit or transfusion service at this facility. Not blood tests, not donation camps alone, and not exchange transfusion for newborn jaundice. | Exchange transfusion is a neonatal procedure. It says nothing about blood on hand for obstetric haemorrhage, and it is behind 5 of the 9 "blood centre" what-if facilities [F]. |
| `anesthesia` | Anaesthesia service for surgery, an anaesthesiology department, an anaesthetist available here, or an anaesthesia machine. Not topical or local anaesthesia, courses, or past jobs. Facts about a named person are `staff_name` only. | Gold already counts the anaesthesiology department (the specialty is the service). Precision here barely moves EmOC grade 3 (see section 2). |
| `ot` | Operating theatre or room at this facility. Not training courses. | Feeds EmOC grade 3: 38 of 43 grade-3 facilities have `ot`. |
| `h24` | Round-the-clock service. | Bonus note only. The fix is the hyphen bug. |
| `nicu` | NICU, SNCU, special newborn care unit. | Absorbs the neonatal cases that leave `icu`. |
| `dialysis` | Provides dialysis, including a working dialysis unit or machines. Not nephrology, kidney stones, transplants, or "cured without dialysis". | Matches the gold rule. |
| `dialysis_unit` | Concrete infrastructure: a named dialysis unit, centre or wing, dialysis machines, beds or stations. A nephrologist is not a unit. | Removes the 44-facility bio jump and the 13 bio-only grade-3 facilities [D]. |
| `icu` | Adult or paediatric ICU (MICU, SICU, CCU, ICCU, CTICU, PICU, ICU beds). A neonatal ICU alone is `nicu`. HDU alone is not `icu`. A department name is not `icu`. | Fixes the 77-facility neonatal error. |
| `ventilator` | Ventilators, or invasive or non-invasive ventilation named as ventilation. CPAP, BiPAP or high-flow oxygen alone is not. | Matches the gold rule (the v1 draft counted BiPAP; gold does not). Neonatal ventilators still count (Risk 8). |
| `emergency` | Emergency, casualty, trauma or ambulance service. A trauma word in a name counts. | Matches the gold rule. Not used by grades. |
| `scheme` | Public schemes: PM-JAY spellings, CGHS, ECHS, ESI or ESIC, state schemes. Not private insurers, not "Ayushman" as a name, not ESI meaning epidural steroid injection. | Feeds the cost-barrier story in WP6. |
| `negated` | New. The fact says a service is absent, unavailable, closed, referred out, or not listed. The absent service gets no tag. Other services the same fact offers keep theirs. | Gives the student something positive to learn for absence, and lets the report count explicit absence. Scoped to the absent service so mixed facts keep their other tags (section 7). |
| `other_clinical`, `staff_name`, `reputation`, `location`, `accessibility`, `photo_artifact`, `web_presence` | Keep. Optional for gold and agent labels. | Cheap for the teacher, and they give the "what share of scraped facts are about care at all" headline. Not used in grades. |

General rules, in the same order as the prompt (Appendix A):
1. A specialty, department or degree is not a service. The one exception is the anaesthesiology department.
2. A fact about a named person (name, degree, affiliation, career) gets `staff_name` and no evidence tag, even when the specialty matches a service. A role stated as available for a service here is evidence ("an anaesthetist is on call for operations here" is `anesthesia`).
3. Negation: `negated`, and no tag for the absent service.
4. Other institutions, past jobs, courses, trials and awards are not evidence.
5. A facility name is a claim: it can give `ob_delivery` or `emergency`, never a tag that lifts a grade above 1.
6. `csection` implies `ob_delivery`. No other implied tag. In particular, the v1 draft's rule "`dialysis_unit` implies `dialysis`" is gone. A fact about a working unit gets both tags because of what it says, not because of a rule.

Grade rule change (recipe v1): a corroborated grade needs at least two distinct fact texts among the facility's facts that carry the line's grade-3 rule tags. Otherwise the facility stays at grade 2. On v0 tags this moves 0 of 43 EmOC, 125 of 587 dialysis and 18 of 375 ICU grade-3 facilities to grade 2 [C]. With the v1 unit words and the two-fact rule together, dialysis grade 3 falls from 587 to 442 [C]. Implementation: `grade_line` needs the per-fact tags, so the check goes in `facility_line_grades` (WP3 owns `vf.recipes`; extend it, do not rewrite it).

Considered and rejected:
- Merging `dialysis` and `dialysis_unit`: grade 3 would lose its meaning. The tighter unit definition and the two-fact rule do the job.
- A separate `extractor_meta` tag: only 17 meta-statements have an evidence hit; `negated` covers them.
- An `hdu` or `ambulance` tag: no grade or story uses them.
- Description sentences as facts: no effect on EmOC grade 3 (section 2).

Gold under v1. WP3 labeled all 429 gold texts under its v0 rules (`reports/model_a_results.md`), which already match v1 on ICU versus NICU, specialty versus service, ventilation, and negation (no evidence tag). Four kinds of rows need a second look [I]:
- 8 rows that name a person (Dr) and carry a grade tag. Of these, 3 are `dialysis_unit` (nephrologists) and 2 are `anesthesia`. Under v1 they become `staff_name` only, unless the person is just part of a hospital name ("blood bank at Dr. ... Memorial Hospital" keeps `blood`).
- The 14 rows of the `fm_negation` stratum and 6 rows whose note says absent or negated get `negated` where it applies.
- 4 rows whose note mentions a name and that carry a grade tag other than `ob_delivery`.
- No gold row mentions exchange transfusion.

The reconciler reads only the text, the old tags and the v1 guide, never any model output. They write `labels/gold_facts_v1.csv` (same columns plus `changed_from_v0`) and report the number of rows changed. Every system is scored on both files; v1 scores are the headline. About 15 minutes.

## 4. Labels: three sources, one format

Every label source writes the same record per text: `text_norm`, `tags` (list), `labeled_by`, `recipe_version = v1`, `labeled_at`, `note`. The student trains on one merged label per text, with this priority: teacher (if any) over agent over rules. Gold texts never appear in any of them (section 5).

### 4.1 Rules v1 (always built, and the no-key fallback)

- `REGEX_V1`: the v0 patterns plus the vocabulary fixes in section 2 (blood centre and transfusion service wording, hyphenated round-the-clock, PM-JAY spellings, ventilation, MICU/SICU/PICU/CCU/ICCU, spelling variants), minus "nephrologist" in `dialysis_unit`.
- Labeling functions on top: neonatal phrases deleted before the `icu` test; "exchange transfusion" removes `blood`; named-person facts lose evidence tags (pattern on "dr." or "dr " plus a name, and "is a ... specialist / consultant / affiliated with"); the negation veto of section 7.
- Freeze before scoring: write `REGEX_V1` and the labeling functions, then compute `sha256` of their JSON-serialized patterns and record it in the report and in every sidecar. Score on gold only after that. Any later change is a new system with a new hash, reported as such. Nobody tunes rules on gold rows.

### 4.2 Agent labels (the main path tonight)

Worklist [H], gold texts excluded:

| Worklist | Strings | Why |
| --- | ---: | --- |
| C-section candidates: v0 `csection` hits plus caesarean spelling variants | 358 | Every string that can lift a facility to EmOC grade 2. Labeling them all means EmOC grade 2 depends on reviewed labels, not on rules. |
| Blood candidates: v0 `blood` hits plus blood centre, transfusion, blood component and blood storage wording | 980 | Every string that can lift a facility to EmOC grade 3. |
| Evidence-tagged facts with a negation cue, outside the two lists above | 63 | Negations that can flip ICU or dialysis grades. |
| S4 negation and meta pool | 1,031 | Examples for `negated`. Lowest priority. |
| Total | 2,432 | The C-section and blood candidates cover 2,159 fact rows. |

How: the agent sees only the text, in shuffled chunks of 100, with the v1 definitions (Appendix A rules and Appendix D). For each string it labels the 9 grade tags, `nicu`, `h24` and `negated`, with a short note on judgment calls. For those 12 tags the agent label replaces the rule label; all other tags keep the rule label. The work can be split across 2 or 3 agents in parallel. Give 100 strings to two agents and report their agreement. Estimate: 1 to 1.5 agent-hours in total, about 30 to 45 minutes of wall time when split.

Output: `labels/agent_facts.parquet` with `labeled_by = "agent:<model id>"` and a `worklist` column, plus the usual sidecar. This is a new file in the contract, so the orchestrator logs it. It is never gold and is never merged into gold.

Caveat: gold was also labeled by an agent. Shared blind spots between agent labels and agent gold would make Model A look better on gold than it is. The facility audit and Hruday's blind labels (decision 4) are the checks.

### 4.3 The teacher (upgrade if a key arrives by about 22:00 ET)

Haiku 5.5 facts (checked against the Claude API reference today): $0.10 and $0.50 per million input and output tokens, half that through Message Batches; `temperature`, `top_p` and `top_k` return a 400; no assistant prefill; a 512-token minimum for prompt caching; a newer tokenizer that produces about 30% more tokens than older models; `thinking: {"type": "disabled"}` is accepted at effort `high` or below; effort defaults to `medium`; refusals have no server-side fallback.

| Setting | Value | Why |
| --- | --- | --- |
| Model | `claude-haiku-5-5` (`VF_LLM_MODEL` in `.env`) | The configured model. |
| Output format | Structured outputs: `output_config.format` with a JSON schema whose tag items are an `enum` of the 21 tags | Parseable JSON with no invented tags. Replaces prefill. |
| Sampling | Send no `temperature`, `top_p` or `top_k` | They return a 400. The disk cache gives reproducibility, and the self-consistency run measures noise. |
| Thinking and effort | Chosen by the pilot from three arms: thinking disabled at effort `low`; adaptive at `low`; adaptive at `medium` | Disabled thinking is the cheapest and least variable setting for a fixed schema. Let the pilot decide on quality. |
| Facts per request | 40, shuffled across strata | Larger batches risk skipped or misaligned ids. |
| `max_tokens` | 8,000 | Thinking counts toward it. A 40-fact answer is about 600 to 700 tokens. |
| Caching | `cache_control: {"type": "ephemeral"}` on the system block | The system prompt is above the 512-token minimum. |
| Transport | Synchronous for the pilot, Message Batches for bulk | Half price, no rate-limit handling on a new key. |

Validation: every id 1 to 40 appears exactly once and `stop_reason` is `end_turn`. On `max_tokens`, split the request in half and retry. On `refusal`, split in half; a single fact still refused gets `tags = null` and is counted in the report.

Cache: one record per fact in an append-only store under `paths.labels_dir() / "teacher_cache"`, keyed by `sha256(model | prompt_version | effort | thinking | replicate | text_norm)`. Production runs use `replicate = 0`. Each pilot arm has its own effort and thinking values, so arms never share entries. The self-consistency rerun uses `replicate = 1`. Both checks also pass `--no-cache-read` and assert that they made API calls: the number of calls equals the number of requests, and the summed `usage.output_tokens` is above zero. Without this, the pilot comparison and the self-consistency run would be served from cache and report perfect agreement. Save every raw response with its `usage` block.

Spend: price `usage` at the Haiku 5.5 rates (input $0.10, 5-minute cache write $0.125, cache read $0.01, output $0.50 per million; half for batches). Stop at `VF_LLM_BUDGET_USD` minus $5, because the cap is shared. Before the first bulk job, run `client.messages.count_tokens` (free) on three real requests and replace the estimates below.

Pilot (about 30 minutes):
1. Draw 200 facts from the training pool (never gold). Run each of the three arms on them, synchronously.
2. Score the three arms on 50 agent-labeled strings from the worklist (which already exist on the no-key path). Run each arm a second time with `replicate = 1` on the same 200 facts to measure self-consistency.
3. Choose the cheapest arm that is within one error of the best arm on the 50 and agrees with itself on at least 98% of fact-tag decisions.
4. Fix the prompt only for systematic errors in that spot-check. Never tune the prompt on gold.

What the teacher labels, in order: the core set (training pool, test split and region-only texts: 110,558 texts in 2,764 requests [M]) in one batch, then optionally everything else (164,108 texts) as a teacher-everywhere baseline. The second batch is not a test set. Report the student's false-positive rate on it against the teacher as "background negatives" only.

Cost estimate [M] (characters measured; 2.4 to 3.4 characters per token assumed; thinking either off or 1,500 tokens per request):

| Job | Texts | Batch, thinking off | Batch, ~1,500 thinking tokens per request |
| --- | ---: | ---: | ---: |
| Pilot and self-consistency (synchronous: 3 arms, 2 runs, 200 facts, 30 requests) | 200 | under $0.05 | under $0.05 |
| Core set | 110,558 | $0.44 to $0.62 | $1.47 to $1.66 |
| Everything else | 164,108 | $0.59 to $0.83 | $2.12 to $2.37 |

Even a threefold error in the token ratio leaves the whole job well under the $30 cap.

With a teacher, Model A is the same student retrained on teacher labels. Report teacher-versus-agent agreement on the 2,432 worklist strings as a check on both.

## 5. Splits: what trains, what tests

All splits are fixed before any training, by `scripts/wp4_splits.py` (new), which writes `labels/model_a_splits.parquet` (`text_norm`, `stratum`, `split`, `component`, `fold`, `inclusion_prob`) with a sidecar.

1. Gold exclusion. Remove every gold `text_norm` (429) and every text within character 3-to-5-gram TF-IDF cosine 0.9 of a gold text from all labels and splits. Report how many were removed.
2. Strata [G]: S1, any v0 hit on a grade-relevant tag (16,235); S2, an `h24` or `emergency` hit only (7,533); S3, near-miss vocabulary (7,264); S4, negation or meta cues (1,032); S5, everything else (243,031). The S3 and S4 regexes are in `model_a_numbers.py`. "closed" stays in the S4 sampling regex, but not in the negation veto.
3. Region holdout. Assam, Kerala, Odisha and Rajasthan were chosen by hand for regional spread before any training. They hold 1,122 facilities (11.2%) and 52,048 fact rows (11.5%). Of the 39,372 distinct texts at their facilities, 30,553 appear nowhere else. Those region-only texts are left out of all training labels. Texts they share with other states stay in training, as they would when the model is deployed on a new dataset. Region-only texts include 29 v0 `csection`, 134 `blood`, 305 `icu` and 185 `dialysis` hits [G].
4. Test split, 15% of everything, in every stratum. For S1 to S4 the unit is the near-duplicate component: connected components of the graph that links texts at cosine 0.9 or more, built over all S1 to S4 texts plus the S5 training sample (51,711 texts, about 45 seconds and 2 GB of memory; the largest component has 91 texts) [G]. A whole component goes to test when the hash of its smallest signature falls under 15%. For S5 the unit is a signature group (digits replaced, filler words dropped, word set sorted). Every text has inclusion probability 0.15, so the test split has the natural distribution. It has 40,684 texts, including 41 of 332 v0 `csection`, 116 of 705 `blood` and 403 of 3,031 `icu` texts [G].
5. Training pool: all S1 to S4 texts that are not in test, region-only or gold, plus a random 20,000 of the remaining S5 texts. That is 43,939 texts (S1 12,064, S2 5,606, S3 5,512, S4 757, S5 20,000); S5 rows carry inclusion probability 0.109, so weight 9.2 [G].
6. Cross-fitting: 5 folds over the training pool, grouped by component (scikit-learn `GroupKFold`).

Why components: a random 10% validation split by text leaves 16.6% of validation texts with a near duplicate in training, a split by signature group 9.0%, and a split by component 0% by construction [K]. With the component split, 0.2% of S1 to S4 test texts and 2.8% of S5 test texts have a near duplicate in the pool [K]. Every agreement number is still reported twice: for all test texts, and for test texts with no near duplicate in training.

## 6. The student (WP4)

### 6.1 Student 1, the default

TF-IDF (word 1 to 2 grams plus character 3 to 5 grams, `char_wb`) and one-vs-rest logistic regression, one classifier per tag, so a fact can get several tags or none (multi-label). No install. Reviewer timing: 21 s to train 21 tags over 4 values of `C` on 30,000 texts, and 6.8 s to tag all 275,097 distinct texts. On the 43,939-text pool with 5-fold cross-fitting, expect a few minutes (estimate).

How the pieces work:
- TF-IDF turns a fact into a sparse vector of weighted word and letter fragments. A fragment gets a high weight when it is frequent in this fact and rare overall. Character fragments absorb spelling variants (anaesthesia, caesarean).
- Logistic regression turns that vector into a probability per tag. A threshold per tag turns probabilities into tags (section 7).

### 6.2 Stretch work, not tonight's default

Only after the core (section 10, steps 1 to 8) is done and swap is below about 50%:
- Student 2: sentence embeddings (`bge-small-en-v1.5`) plus logistic regression, alone and concatenated with TF-IDF. Needs `torch` and `sentence-transformers` (about 1 GB). The distinction that matters most, ICU versus NICU, is lexical, so the expected gain is small.
- Student 3: fine-tuning a small encoder.
- A local Ollama teacher (`gemma4:12b`, about 7.7 GB on disk): only with Hruday's yes (decision 6).

### 6.3 Training details

- Labels: the merged label per text (section 4). Add `ob_delivery` wherever `csection` is set, in every label source and in student output.
- Weight each text by `sqrt(n_occurrences)`. Frequent strings matter more for grades, but full frequency weights would let a few strings dominate.
- Compare `class_weight=None` and `"balanced"` and 4 values of `C` on out-of-fold scores.
- Evaluate with the model fit on the training pool. Then refit with the chosen `C` and thresholds on the pool plus the test split plus the region-only texts, and ship that refit model. Test and region numbers come from the model fitted before the refit. The facility audit reads the shipped model's grades, because those are what the map shows.
- Training-fit check: list every worklist string where the shipped model disagrees with its own agent label. Expect a handful. The audit reads any facility whose grade they change.
- Save the model, vectorizers, tag list, thresholds, label-file hashes and `REGEX_V1` hash under `paths.data_dir() / "models" / "model_a" / <version>`.

### 6.4 Label-source ablation

Train the same student three ways on the same pool: rules only; rules plus agent labels (tonight's Model A); and teacher labels if a key arrives. With model capacity held fixed, the differences on gold and in the audit show what each label source is worth. This is the cleanest experiment in Model A. If "rules plus agent" adds little over rules, the report says so.

## 7. Thresholds, multi-label, and negation

The facility rules ask whether any fact at the facility has tag X, so small per-fact error rates become large facility error rates.
- About 450,000 fact rows are not C-section facts. At a per-fact false-positive rate of 0.1%, about 450 facts get a false `csection`. With 45.4 facts per facility, 1 - 0.999^45.4 = 4.4% of the 9,929 facilities with facts would get one: about 440 facilities, next to 444 at grade 2 or above today. At 0.01% the damage falls to about 45 facilities.
- This arithmetic assumes independent errors per fact. Errors are not independent: they cluster by string, and 36 strings cover half of all C-section mentions [E]. One wrong call on "caesarean section" moves 70 facilities. Hence the exhaustive agent labels on C-section and blood strings, the facility-weighted precision in section 8, and the audit.

Thresholds:
- Grade tags (`ob_delivery`, `csection`, `blood`, `anesthesia`, `ot`, `dialysis`, `dialysis_unit`, `icu`, `ventilator`): the threshold that maximizes recall subject to estimated natural-distribution precision of at least 0.95 against the training labels.
- Other tags: the threshold that maximizes F1.
- Estimation: 5-fold out-of-fold probabilities over the whole training pool, folds grouped by component. Reweight each row by the inverse of its inclusion probability (S5 rows weight 9.2, all others 1) so counts match the natural distribution. A precision of 0.95 measured on a few dozen positives is weak; report the number of positives behind each threshold.
- Never choose thresholds on gold, the test split, the region holdout or the audit.

Post-processing, applied the same way to every system:
1. Independent per-tag thresholds.
2. Negation veto, scoped. A negation cue "attaches" to tag t when it lies in the same clause (split at `;`, `.`, " but ", " though ") within 40 characters of a `REGEX_V1` keyword for t. When the student's `negated` passes its threshold, drop each evidence tag that a cue attaches to. If `negated` passes but no cue attaches to any tag (a paraphrased absence such as "patients needing dialysis go to the medical college"), drop all evidence tags and put the fact on the audit list. The rule backstop applies the same attachment test with a cue list that has no "closed" and no bare "referral": not available, unavailable, not listed, not functional, non-functional, lacks, no, without, referred to, referred out, sent to. Report results with and without the backstop.
3. Hierarchy: add `ob_delivery` to any `csection`. Nothing else.

Keep per-tag probabilities for the nine grade tags in `gold/fact_tag_scores.parquet` (outside the contract, so it breaks nothing). WP4 picks evidence quotes by probability from it, and it supports a threshold sensitivity check.

## 8. Evaluation

### 8.1 Systems in the results table

| System | Labels from | Purpose |
| --- | --- | --- |
| Regex v0 | Recipes doc as written | The documented baseline. Fact-level tables only. |
| Regex v1 | v0 plus the fixes, frozen with its hash | The baseline for every map-level comparison and for the video. |
| Student on rules only | Rules v1 | Ablation (section 6.4). |
| Model A | Rules v1 plus agent labels (no key), or teacher labels (key) | The shipped tagger. |
| Teacher, if a key arrives | Haiku 5.5 | A labeler and a comparison row. It never replaces Model A on the map. |

### 8.2 Facility audit (the headline)

Scope:
- EmOC: every facility at grade 3 under any system (regex v0, regex v1, Model A, teacher), and every facility whose EmOC grade-2+ or grade-3 membership differs between regex v0, regex v1 and Model A. Expected under 200 facilities, probably under 100.
- ICU and dialysis: a random 30 from each change set (v0 to v1, v1 to Model A, separately for entering and leaving grade 2+ and grade 3), plus 30 random facilities that stay at grade 3 under every system. Read these after EmOC.
- Every fact on the negation audit list (section 7) whose facility changes grade.

Procedure: for each facility the auditor reads the evidence quotes and every fact carrying a tag from the line's rule, without seeing which system produced which grade, and records the grade the evidence supports and a verdict: correct, too high, too low, or cannot tell. The output is `labels/facility_audit.csv` (`facility_id`, `service_line`, `why_selected`, `grade_v0`, `grade_v1`, `grade_model_a`, `grade_teacher`, `audited_grade`, `verdict`, `auditor`, `note`) plus a sidecar, as a new contract entry. Split it across agents, and give Hruday 20 facilities blind if he has time (decision 4).

Metrics: facility-level precision of grade 3 per system (share of the system's grade-3 facilities the auditor puts at 3), with Wilson intervals; for each change set, the share of moves the auditor agrees with. Time box: 60 to 90 agent-minutes for EmOC.

### 8.3 Gold (the only fact-level truth)

- `labels/gold_facts_v1.csv` (headline) and `labels/gold_facts.csv` (v0), 429 texts. Never used for training, validation, prompt edits, rule edits or thresholds.
- Per tag: precision, recall and F1, with the number of gold positives beside each, weighted by stratum (`stratum` and `weight` in `labels/gold_sample.csv`, joined on `text_norm`), with stratified bootstrap intervals. Compare systems with a paired bootstrap on the same rows, and claim a difference only when its interval excludes zero. With about 25 positives per tag, differences under about 0.15 on a single tag mean nothing.
- Report recall separately for the near-miss rows (`nm_*` strata, 77 rows) and all other rows. The near-miss strata were found with keyword lists that overlap the vocabulary regex v1 adds, so regex v1 and anything trained on it will look good on them. Recall on vocabulary outside those lists is not measured by gold beyond its 30 random rows.
- Macro F1 over the nine grade tags is the methods-card number, next to the audit's facility precision.

### 8.4 Agreement with the labels (not truth)

- Test split: per-tag agreement between Model A and its label source, for all test texts and for test texts with no near duplicate in training.
- Region test: the same on the 30,553 region-only texts, plus facility grade-set agreement (Model A versus labels) for the 1,122 facilities in those states. With a teacher, this directly tests "runs on new regions with no API key". Without one, it tests whether the student reproduces rules and agent judgments on strings it never saw.
- Facility-weighted precision on the flagship strings: for each system, precision of `csection` and `blood` against the agent labels on the 358 and 980 candidate strings, each string weighted by the number of facilities it appears at. For Model A, use the cross-fitted out-of-fold predictions, so no string is scored by a model that trained on it.
- Out-of-vocabulary probe: read the 50 S5 texts with the highest out-of-fold Model A probability for each of `csection`, `blood`, `icu` and `dialysis_unit` (200 texts, about 10 minutes), and count the real positives. With a teacher, also read 50 random teacher grade-tag positives from outside S1 and S3. These are positives no keyword list anticipated; their count is the honest evidence of whether learned labels add recall over rules.
- Teacher noise ceiling (key path): the self-consistency agreement from the pilot.

### 8.5 Map level

For each service line, and for regex v0 to regex v1 and regex v1 to Model A as separate comparisons: the number of facilities entering and leaving grade 2+ and grade 3, Jaccard overlap of the before and after sets, the 4 by 4 transition matrix, and the distance summary of section 9. No weighted kappa: grades 0 and 1 dominate it and it hides what the map shows. If Model A adds little over regex v1 on the map, the report says so; that is still a result.

Everything goes into `gold/model_a_report.json`: which labels trained Model A (first field), the `REGEX_V1` hash and label-file hashes, per system and tag the gold metrics with intervals and counts, audit results, test and region agreement, the out-of-vocabulary count, thresholds with their positive counts, grade-set changes, cost and speed, the gold-exclusion statement, and near duplicates removed.

## 9. How grades move downstream, and how to show it

The tagger changes the answer through grades, and grades change distances. Recomputed what-ifs, starting from regex v0 (straight-line distance from 151,010 geocoded post offices to the nearest facility at that grade; facilities without valid coordinates drop out) [F]:

| Change | Facilities | Median distance | 90th percentile | Share beyond 60 km | Post offices moved more than 10 km |
| --- | ---: | ---: | ---: | ---: | --- |
| EmOC grade 3, regex v0 | 43 | 145.5 km | 306.2 km | 84.4% | |
| plus 5 facilities whose only "blood" is neonatal exchange transfusion (not blood under v1) | 48 | 129.7 km | 279.2 km | 81.7% | 22,995 closer |
| plus 3 plausible blood services (blood centre, component transfusion) | 46 | 140.5 km | 306.1 km | 83.5% | 4,973 closer |
| of which, the one clear "on-site 24x7 blood centre" | 44 | 145.5 km | 306.2 km | 84.4% | 0 |
| plus all 9 candidates (the v1 draft's headline; an upper bound) | 52 | 123.7 km | 279.0 km | 80.4% | 29,944 closer |
| ICU grade 2 or 3, regex v0 | 2,049 | 29.5 km | 76.2 km | 17.2% | |
| minus 77 facilities with ICU evidence only from neonatal mentions (neonatal phrases deleted, then re-tested) | 1,973 | 30.1 km | 79.1 km | 18.2% | 3,258 farther |
| minus 202 (the v1 draft's broader test; an upper bound) | 1,848 | 31.6 km | 80.6 km | 19.8% | 10,683 farther |

The ninth candidate is a job-qualification text ("diploma in transfusion medical technology").

The lesson for the story: grade 3 is so thin that five ambiguous facts move 23,000 post offices, while the one unambiguous on-site blood centre moves none. That argues for precision and a facility audit, not for recall gains. Model fixes also push both ways: recall fixes add facilities, precision fixes remove them.

Rules for anything shown:
1. A what-if, an evidence card or a map toggle appears in the video only after a person has read every facility in it.
2. Comparisons on screen are regex v1 versus Model A, never v0 versus Model A. Regex v1's own fixes are reported as regex v1's.
3. Quadrant changes in WP6 are described as evidence being reclassified, not as validated changes in care.

Freeze rule and requests to WP6. Graded access mostly measures where web-visible private hospitals are [L]. Across 705 districts, district median distance to EmOC grade 2+ correlates with the NFHS C-section rate at Spearman -0.33, but distance to any facility in the dataset correlates at -0.38. After controlling for women's schooling, household electricity and clean cooking fuel, the graded-access correlation is +0.07, and +0.06 after also controlling for distance to any facility. Graded and any-facility distance correlate equally (-0.19) with the share of births outside public facilities. So Model B cannot validate Model A. The rules:
- The taxonomy, `REGEX_V1`, the student and its thresholds are frozen on gold and the audit before WP6 runs, and are never chosen by Model B error.
- Ask WP6 to add "distance to any facility" as a placebo access feature. Model A cannot change it, unlike `acc_g1_logp50`, which depends on `ob_delivery` tags. Model B's current targets (institutional births as the primary target, public-facility C-section rate as validation) already fit; keep them.
- Ask WP6 not to describe a change in its M2 versus M3 contrast under Model A grades as a result for Model A.

What WP4 produces for WP5, WP6, WP8 and WP9:
1. Grade-set changes and transition matrices per service line: regex v0 to regex v1, and regex v1 to Model A.
2. For each, the distance summary above for grades 2 and 3, and the count of post offices whose nearest graded facility moves more than 10 km. WP5 owns the real rerun; WP4 computes a quick version with a BallTree.
3. Five to ten audited evidence cards: the fact, the regex v1 tag, the Model A tag and the grade change. Good candidates: a NICU-only hospital that loses its ICU grade, a nephrologist bio that no longer makes a dialysis unit, a "blood bank not available" fact, the on-site 24x7 blood centre.

How to show it: a regex v1 versus Model A toggle on the map (WP8's call) and one video beat: "the rules say X facilities offer corroborated emergency obstetric care; the trained tagger, checked facility by facility, says Y, and here is the fact that made the difference". The methods-card figure is the audit result next to the grade-set changes.

## 10. Build steps for tonight

Files (ownership: WP3 owns `src/vf/recipes.py` and the recipes doc; extend what the WP3 agent wrote, do not rewrite it):
- `src/vf/recipes.py`: `TAGS_V1` with definitions, `REGEX_V1` and its hash, the labeling functions, the negation veto, and the two-fact rule for corroborated grades.
- `src/vf/tagging.py` (new, shared by WP3 and WP4): post-processing, metrics with bootstrap intervals, the gold loader with near-duplicate exclusion, the near-duplicate graph.
- `scripts/wp4_splits.py`, `scripts/wp4_agent_worklist.py`, `scripts/wp4_train.py`, `scripts/wp4_evaluate.py`, `scripts/wp4_apply.py`, `scripts/wp4_audit_sheet.py`. With a key: `scripts/wp3_teacher_label.py`.
- Outputs, each written atomically with a `.meta.json` sidecar (row count, created time, `git rev-parse HEAD`, recipe version `v1`, model version, input hashes): `labels/model_a_splits.parquet`, `labels/agent_facts.parquet`, `labels/gold_facts_v1.csv`, `labels/facility_audit.csv`, `gold/fact_tags.parquet`, `gold/capability_grades.parquet`, `gold/fact_tag_scores.parquet`, `gold/model_a_report.json`. Four of these are new to the contract; the orchestrator logs them.

Order and time, no key (the default):

| Step | Work | Time |
| --- | --- | --- |
| 1 | `TAGS_V1`, `REGEX_V1`, labeling functions, two-fact rule; freeze and hash; tests | 45 min |
| 2 | Splits (gold exclusion, region, components, test, pool, folds); export the worklist | 20 min |
| 3 | Agent labels on the 2,432 strings, split across 2 or 3 agents, in parallel with steps 4 and 5 | 30 to 45 min wall |
| 4 | Gold v1 reconciliation (about 30 rows) | 15 min |
| 5 | Student on rules only: cross-fit, thresholds, test and region agreement (the ablation) | 30 min |
| 6 | Model A on rules plus agent labels: cross-fit, thresholds, refit, tag all facts, regrade with v1 rules | 30 min |
| 7 | Gold scoring of all systems, facility-weighted precision, out-of-vocabulary probe | 30 min |
| 8 | Facility audit, EmOC first | 60 to 90 min, can be split |
| 9 | Grade-set changes, quick distances, report; tell WP5 to rerun | 30 min |
| Stretch | Section 6.2, only if swap is below about 50% | |

Core total: about 4.5 to 5 hours of work, about 3.5 hours of wall time with parallel agents.

If a key arrives by about 22:00 ET: install `anthropic` (`uv pip install --python .venv/bin/python -e ".[llm]"`), run `count_tokens`, the pilot (30 min), and the core-set batch (about an hour, mostly waiting). Then repeat steps 6 to 9 with teacher labels, auditing only facilities whose grades differ from the no-key Model A. After 22:00, ship the no-key Model A.

## 11. Risks

1. Shared bias. Gold, agent labels and a possible teacher are all Claude. Mitigation: the facility audit, Hruday's 50 blind gold labels and 20 blind audit facilities (decision 4), and agent-to-agent agreement on 100 strings.
2. Definition drift between gold and v1. Mitigation: the reconciliation pass, both gold files scored, and rows changed reported.
3. Facility-level false positives from correlated string errors (section 7). Mitigation: exhaustive agent labels on flagship strings, precision-first thresholds, the audit.
4. The student copies its labels' mistakes. On the no-key path it is a learned copy of rules plus agent judgments, and the report says so. The label-source ablation shows how much the agent labels add.
5. Small gold. About 25 positives per tag gives intervals of about plus or minus 0.14. Mitigation: intervals, paired tests, the audit, plain wording.
6. Memory pressure (swap at 95% at 15:20 ET). The near-duplicate graph peaks at about 2 GB for under a minute; run it when no other heavy job is running. Stretch work waits for swap below about 50%.
7. Thin test cells: 41 C-section texts in the test split and 29 in the region set. Agreement there is a sanity check, not a precise estimate; the facility-weighted out-of-fold precision on all 358 C-section strings carries more weight.
8. Neonatal ventilators: a facility with an adult ICU fact and only a neonatal ventilator fact still reaches ICU grade 3 (34 facilities at v0). Leave the rule alone tonight and note it; a v2 rule could require a non-neonatal ventilator fact.
9. Facts about the wrong organization (contaminated source URLs). Rule 4 catches some; most cannot be detected from the fact alone. The audit may find some; state it as a limit.
10. API practicalities (key path only): unknown rate limits on a new key (use Batches), batches can take up to 24 hours (fall back to synchronous calls with modest concurrency), refusals (split, retry, count), shared budget (stop with a $5 margin).
11. The student's pitch. At Haiku 5.5 batch prices the teacher costs about $0.40 to $1.50 per 100,000 facts [M], so the case for the student is not money. It is free, offline, deterministic, versioned and thousands of times faster, so it can run over a new dataset without an API key or sending data out. The region test is the evidence for that claim.

## 12. Decisions for Hruday

1. Taxonomy v1 as frozen in section 3 (named-person facts are `staff_name`, `negated` added, no dialysis implication, two distinct facts for grade 3, gold reconciled)? Default: yes. The orchestrator logs it now as a default.
2. The map uses Model A's tags in both paths; a teacher is a labeler and a comparison row, never the map's tagger. Default: yes. This replaces the v1 draft's default of using teacher tags when the teacher wins on gold.
3. No key by about 22:00 ET: ship the no-key Model A and mark the teacher row "not run". Default: yes.
4. Label 50 gold texts blind (about 20 minutes) and audit 20 facilities blind (about 10 minutes)? Default: strongly recommended. It is the only check on agent-labeled gold and agent labels.
5. A what-if appears in the video only after a person has read every facility in it. Default: yes.
6. Local Ollama models (`gemma4:12b`) as a fallback teacher? Default: no tonight, because of memory.
7. Install `torch` and `sentence-transformers` for Student 2? Default: no tonight; stretch only.
8. A stronger adjudicator (`claude-sonnet-5-5`, about $5) for teacher and regex v1 disagreements on grade tags (key path)? Default: only if the teacher's gold F1 on a grade tag is below 0.8.

## Appendix A: teacher system prompt (v1.1)

Use verbatim. The agent labeler uses the same rules and tag definitions. Changing the prompt changes `prompt_version` and so the cache key, which is intended. Every quoted example is a paraphrase written for this prompt: none is a fact in `facts.parquet`, and none is within cosine 0.9 of a fact or a gold row; the closest is 0.80 [J].

```text
You label short facts that were scraped from the web pages of health facilities in India. Each fact is one sentence or phrase about one facility. Your labels train a smaller model and decide what evidence a facility has for three services: emergency obstetric care, dialysis, and intensive care with ventilation.

For each fact, return every tag that applies, or an empty list. Judge only what the fact itself says. Do not guess what a facility probably has.

Core rules
1. Evidence means the fact says this facility provides the service, has the unit, or has the equipment. A specialty, department, or degree name alone is not evidence of a service: "Our nephrology department is one of twenty specialties" is not dialysis, "Gynaecology and obstetrics appears among our specialties" is not ob_delivery, and "Critical care medicine appears in the specialty list" is not icu. One exception: an anaesthesiology department counts as anesthesia, because anaesthesia is that department's service.
2. A fact about a named person (a doctor's name, degree, affiliation, or career) gets staff_name and no evidence tag, even when the person's specialty matches a service. "Dr. Iyer, a kidney specialist, consults at this hospital" gets [staff_name] only. A fact that says a role is available for a service at this facility is evidence: "An anaesthetist is on call day and night for operations here" gets [anesthesia, h24].
3. If the fact says a service is absent, not available, closed, not functional, referred elsewhere, or says the information was missing from its source, tag negated and give that service no tag. If the same fact also offers other services, tag those as usual. "Patients who need a caesarean are sent on to the district hospital" gets [negated]. "Six-bed ICU on site, though there is no blood bank" gets [icu, negated].
4. Facts about another institution, a past job, a teaching course, a training program, a research trial, or an award are not evidence for this facility. "Was earlier a lecturer in anaesthesia at a Ludhiana medical college" gets [staff_name].
5. A facility name is a claim. A maternity or trauma word in a name can give ob_delivery or emergency, but never any other evidence tag. "Lotus Kidney and Dialysis Care is listed on the city map" gets [web_presence] only.
6. Implied tag: csection always also gets ob_delivery. There is no other implied tag.
7. A fact can have several tags. "Twelve-bed ICU, every bed with a ventilator, staffed round the clock" gets [icu, ventilator, h24].

Evidence tags
- ob_delivery: conducts deliveries, or has a maternity service, labour room, or labour ward. Yes: "Labour ward staffed by an obstetrician at all hours". No: "Antenatal scans offered in the second trimester" (other_clinical).
- csection: performs caesarean sections (also LSCS, C-section, caesarean delivery). Yes: "Both normal and caesarean births are handled here". No: "Patients who need a caesarean are sent on to the district hospital" (negated).
- blood: blood bank, blood centre, blood storage unit, or transfusion service at this facility. Yes: "Runs its own blood centre that never closes". No: "Collects samples for blood sugar and haemoglobin tests" (other_clinical); "Exchange transfusion for jaundiced newborns" (a newborn procedure, other_clinical, not a blood service).
- anesthesia: anaesthesia service for surgery, an anaesthesiology department, an anaesthetist available here, or an anaesthesia machine or workstation. Yes: "Anaesthesia workstation in each of the two theatres" gets [anesthesia, ot]. No: "Cataract removal done under numbing eye drops" (other_clinical).
- ot: an operating theatre, operation theatre (OT), surgical suite, or operating room at this facility. Yes: "Theatre complex of three modular OTs". No: "Diploma course for operation theatre assistants" (a course, no tag).
- h24: the facility or a service runs 24 hours, 24x7, or round the clock. Yes: "Casualty stays open through the night, every day of the year" gets [emergency, h24]. No: "Outpatient clinic runs from 10 am to 4 pm".
- nicu: neonatal intensive care, NICU, SNCU, or a special newborn care unit. Yes: "Level III neonatal unit with fourteen intensive care cots". No: "Advice on breastfeeding and newborn vaccination" (other_clinical).
- dialysis: provides dialysis (haemodialysis, peritoneal dialysis, CRRT, SLED), including a working dialysis unit or dialysis machines at this facility. Yes: "Haemodialysis sessions run in two shifts daily". No: "Kidney stones broken up with shock waves" (other_clinical); "Claims to reverse kidney failure without any dialysis" (other_clinical).
- dialysis_unit: concrete dialysis infrastructure at this facility: a named dialysis unit, centre, or wing, or dialysis machines, beds, or stations. A nephrologist is not a unit. Yes: "Dialysis wing with eight haemodialysis machines" gets [dialysis, dialysis_unit]. No: "Dialysis can be arranged on request" (dialysis only).
- icu: an adult or paediatric intensive or critical care unit at this facility: ICU, MICU, SICU, CCU, ICCU, CTICU, PICU, or ICU beds. A neonatal ICU alone is nicu, not icu. A high dependency unit (HDU) alone is not icu. Yes: "Medical ICU with 18 monitored beds". No: "Intensive care for sick newborns (NICU)" gets [nicu].
- ventilator: mechanical ventilation: ventilators, or invasive or non-invasive ventilation named as ventilation. CPAP, BiPAP, or high-flow oxygen alone is not. Yes: "Patients in respiratory failure can be put on invasive or non-invasive ventilation". No: "Airy and well-ventilated patient rooms"; "CPAP machine for sleep apnoea at home" (other_clinical).
- emergency: emergency, casualty, accident, trauma, or ambulance service at this facility. Yes: "Accident victims are received at any hour" gets [emergency, h24]. No: "Counselling on emergency contraceptive pills" (other_clinical).
- scheme: accepts a public health insurance scheme: Ayushman Bharat, PM-JAY, CGHS, ECHS, ESI or ESIC, or a state scheme such as MJPJAY, Aarogyasri, Chiranjeevi, CMCHIS. Yes: "Cashless treatment under PM-JAY for card holders". No: "Back pain treated with an epidural steroid injection (ESI)" (other_clinical).

Other tags (use these so we can see what the facts are about)
- negated: the fact states something is absent, unavailable, closed, referred out, or not listed. See rule 3.
- other_clinical: a real clinical service, procedure, or piece of equipment that fits none of the evidence tags. Yes: "Video EEG monitoring for epilepsy patients". No: "Rated 4.6 stars by 120 patients" (reputation).
- staff_name: names a person, or describes a named person's qualifications or career. Yes: "Dr. Anita Kulkarni, MBBS, MS (General Surgery)". No: "A team of fourteen doctors" (no tag needed unless something else applies).
- reputation: ratings, reviews, awards, accreditation, rankings, or years of experience. Yes: "Rated 4.6 stars by 120 patients". No: "Neonatal unit with fourteen cots" (nicu).
- location: an address, landmark, or statement of where the facility is. Yes: "Sits two streets behind the old bus stand in Kota". No: "Runs a pharmacy counter inside the building" (other_clinical).
- accessibility: parking, wheelchair access, lifts, ramps. Yes: "Ramp and wheelchair at the main entrance". No: "Ambulance kept ready at the gate" (emergency).
- photo_artifact: describes what is visible in a photo or image rather than stating a service. It can carry evidence tags too. Yes: "Image shows a bed with side rails and an IV stand"; "Theatre lights can be seen in the photograph" gets [ot, photo_artifact]. No: "A total of 85 inpatient beds".
- web_presence: about the web page, listing, or social media itself. Yes: "Has an unofficial page on a social network". No: "Doctors consult patients over video calls" (other_clinical).

Input format: lines of the form "<id>: <fact>".
Return one entry for every id, in the same order, with an empty tags list when nothing applies.
```

## Appendix B: schema, request and cache sketch

Python, `anthropic` SDK (the `llm` extra). The builder runs it on the pilot before any bulk job.

```python
import hashlib
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

TAGS = ["ob_delivery", "csection", "blood", "anesthesia", "ot", "h24", "nicu",
        "dialysis", "dialysis_unit", "icu", "ventilator", "emergency", "scheme",
        "negated", "other_clinical", "staff_name", "reputation", "location",
        "accessibility", "photo_artifact", "web_presence"]

SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"},
                       "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}}},
        "required": ["id", "tags"], "additionalProperties": False}}},
    "required": ["labels"], "additionalProperties": False,
}

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY; load .env first, never print it
SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

def params(texts, effort="low", thinking="adaptive"):
    """thinking: "adaptive" or "disabled" (Haiku 5.5 accepts disabled at effort high or below)."""
    user = "\n".join(f"{i + 1}: {t}" for i, t in enumerate(texts))
    return MessageCreateParamsNonStreaming(
        model="claude-haiku-5-5",
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": thinking},
        output_config={"effort": effort,
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )  # no temperature: Haiku 5.5 rejects non-default sampling values

def cache_key(text_norm, *, model, prompt_version, effort, thinking, replicate=0):
    raw = "|".join([model, prompt_version, effort, thinking, str(replicate), text_norm])
    return hashlib.sha256(raw.encode()).hexdigest()

# Pilot arms: ("low", "disabled"), ("low", "adaptive"), ("medium", "adaptive"); each also run with replicate=1.
# Pilot and self-consistency runs pass no_cache_read=True and assert n_api_calls == n_requests and
# sum(r.usage.output_tokens) > 0.
# Bulk:  batch = client.messages.batches.create(requests=[
#            Request(custom_id=f"core-{k:05d}", params=params(chunk, *chosen_arm)) for k, chunk in enumerate(chunks)])
# Collect: for r in client.messages.batches.results(batch.id): key by r.custom_id (results arrive in any order);
#          on r.result.type == "succeeded": check stop_reason, read the text block, json.loads, validate ids 1..n.
```

## Appendix C: how the numbers were computed

`docs/ml/model_a_numbers.py` recomputes every number marked [A] to [M] from the committed tables. It writes nothing. Run `.venv/bin/python docs/ml/model_a_numbers.py` (about 1 minute, about 2 GB peak) or add `--fast` to skip the near-duplicate graph (15 seconds).
- [A] facts and [B] regex hits: `silver/facts.parquet` joined to `gold/fact_tags.parquet` (the regex v0 tags WP3 wrote).
- [C] grades: `gold/capability_grades.parquet`, 9,989 facilities. Fragility counts distinct `text_norm` per facility for the defining tag. The two-fact count uses the tags of the line's grade-3 rule.
- [D] known errors: the regexes are in the script, one per finding. "Claims wording" is a single regex of department, degree, course, training, specialist and "Dr" words.
- [E] string concentration: facility incidences are the number of facilities per distinct text, summed.
- [F] distances: post offices with valid coordinates (151,010) from `silver/post_offices.parquet`; facilities with valid coordinates; scikit-learn `BallTree`, haversine, Earth radius 6,371 km. The 9 blood candidates are facilities at EmOC grade 2 with `anesthesia` or `ot`, no v0 `blood`, and a fact matching blood cent, transfus, blood storage unit or blood component. They are classified by their candidate facts.
- [G], [H], [K] splits, worklist and leakage: the strata regexes, the signature function, the region list and the component graph are in the script, so `scripts/wp4_splits.py` can copy them.
- [I] gold: `labels/gold_sample.csv` and `labels/gold_facts.csv`.
- [J] prompt examples: the script parses Appendix A of this file and checks each quoted example against every fact and gold row, exactly and by character TF-IDF cosine.
- [L] circularity: WP2's post offices and district keys, `silver/nfhs.parquet`; Spearman correlations of district median distances, and partial correlations after linear adjustment for three development variables.
- [M] teacher cost: characters of the real texts in each job and of the Appendix A prompt; 2.4 and 3.4 characters per token; 0 or 1,500 thinking tokens per request; Haiku 5.5 prices.

Disclosure: while counting the gold reconciliation rows [I], the author of this revision read 8 gold texts that name a person and 13 gold notes that mention a name. The v1 definitions follow WP3's published gold labeling rules; nothing was tuned to gold scores. No `REGEX_V1` pattern exists yet.

## Appendix D: labeling guide, v1 changes from WP3's gold rules

For the gold reconciler and the agent labeler. Everything not listed here follows `reports/model_a_results.md`, "Labeling rules".
1. A fact about a named person (name, degree, affiliation, career) gets `staff_name` and no evidence tag, for every service. This changes the gold rule that a nephrologist is `dialysis_unit` and that a named anaesthesiologist is `anesthesia`. Boundary cases: a named doctor with a degree is `staff_name`; "an anaesthetist is available round the clock for surgery here" is `anesthesia` (and `h24`); "Dr X is a nephrologist associated with Y" is `staff_name`; a person's name inside a hospital name ("blood bank at Dr. ... Memorial Hospital") does not make it a person fact.
2. The anaesthesiology department counts as `anesthesia`, as in gold. No other department counts.
3. Exchange transfusion for newborns is not `blood`.
4. New tag `negated`: the absent service gets no tag (as in gold), and the fact gets `negated`. Other services in the same fact keep their tags.
5. A facility name can give `ob_delivery` or `emergency` only, never a tag that lifts a grade above 1.
6. Noise tags are optional. The 9 grade tags, `nicu`, `h24` and `negated` are required.

## Review log

The review came in at 14:56 ET (scripts `check1.py` to `check7.py` in the session scratchpad, `review_a/`). Each row is one critique and what this revision did. The reviewer's numbers were rerun where this doc uses them, and all matched.

| # | Severity | Critique | What changed |
| --- | --- | --- | --- |
| 1 | critical | Stage 2 held no grade-tag positives, so it could not be the natural-distribution test set or set thresholds | Stage 2 is gone. The test split is a 15% sample of every stratum (inclusion probability 0.15 recorded), built from near-duplicate components. Thresholds come from 5-fold grouped out-of-fold scores over the training pool, reweighted to natural frequency. Texts the teacher labels beyond the core set are "background negatives" with only a false-positive rate (sections 4.3, 5, 7). |
| 2 | critical | The headline what-ifs were mostly artifacts (exchange transfusion; neonatal rule too broad) | Section 9 and the Summary now show the recomputed rows: 5 exchange-transfusion facilities give 22,995 of the 29,944 post offices, 3 plausible facilities 4,973, the clear one 0; the neonatal cut is 77 facilities and 3,258 post offices; the old rows are marked as upper bounds. Exchange transfusion is a "No" example for `blood` in the prompt and in Appendix D. New rule: no what-if on screen until a person has read every facility in it. |
| 3 | major | "Nephrologist on staff" plus the implied `dialysis` turns bios into grade 3 | Named-person facts are `staff_name` only; `dialysis_unit` excludes nephrologists; the implied `dialysis` rule is gone; grade 3 needs two distinct facts (125 dialysis and 18 ICU grade-3 facilities move to grade 2 on v0 tags). Three person boundary examples are in the prompt and Appendix D. The `anesthesia` row now says precision there barely affects EmOC grade 3 (0 of 43). |
| 4 | major | Graded access mostly tracks web-visible private hospitals; Model B cannot validate Model A | Freeze rule added (Summary 6, section 9). Requests to WP6: a "distance to any facility" placebo feature, keep institutional births and the public C-section rate, and do not read M2 versus M3 under Model A grades as a Model A result. On-screen wording rule for quadrant changes. Numbers recomputed [L]. |
| 5 | major | Nothing measured facility-level truth; kappa hides what matters; errors cluster by string | The facility audit is the headline metric (section 8.2), with `labels/facility_audit.csv`. All C-section and blood candidate strings are agent-labeled. Facility-weighted precision on those strings, from out-of-fold predictions. Kappa replaced by grade-set changes and Jaccard (8.5). Section 7 notes that errors are correlated. |
| 6 | major | One word list defines S3, regex v1 and the gold near-miss stratum | `REGEX_V1` is frozen and hashed before any gold scoring (4.1). Gold recall is reported separately for `nm_*` rows. An out-of-vocabulary probe reads high-scoring texts from outside every keyword list, and with a teacher also 50 teacher positives from outside S1 and S3 (8.4). |
| 7 | major | Near duplicates leak between train and validation | Test split and cross-fit folds by near-duplicate component. Measured: split by text 16.6% leakage, by component 0% [K]. All agreement reported with and without near duplicates in training. |
| 8 | major | Map-level comparisons against v0 credit the model with regex fixes | Regex v1 is the baseline in every map-level table and in the video. v0 to v1 and v1 to Model A are reported separately (8.1, 8.5, 9). |
| 9 | major | The cache key would serve the pilot and self-consistency runs from cache | Key now includes effort, thinking and a replicate number. The pilot and self-consistency runs bypass cache reads and assert that API calls happened (4.3, Appendix B). |
| 10 | major | The plan led with blocked work (no key) | The no-key path is the main plan: agent labels on 2,432 flagship strings in `labels/agent_facts.parquet` (never gold), the student trained on rules plus those labels. The teacher is a drop-in until about 22:00 ET. Each case says which artifact is Model A (Summary 1, sections 4, 10). |
| 11 | major | Memory pressure makes the optional steps risky | Student 2, Student 3 and the local LLM teacher are stretch work, only after the core and with swap below about 50% (6.2). The time goes to the audit. The graph step's memory (about 2 GB, under a minute) is measured and stated. |
| 12 | major | If the teacher labels everything, the student's role is untested | Region holdout: Assam, Kerala, Odisha and Rajasthan (11.5% of fact rows); their 30,553 region-only texts never enter training labels, and student-versus-label agreement is reported there (5, 8.4). Decided before training: the map uses Model A's tags in both paths (decision 2). |
| 13 | minor | Prompt examples were verbatim fact texts | All examples rewritten as paraphrases. Checked by script: no exact match to any fact, and none within cosine 0.9 of a fact or a gold row [J]. |
| 14 | minor | Negation backstop cue words misfire, and the veto drops every tag on mixed facts | "closed" and bare "referral" removed from the backstop (kept for S4 sampling). The veto now drops only tags a cue attaches to (same clause, within 40 characters); `negated` is scoped to the absent service in the prompt (section 7, rule 3). |
| 15 | minor | The pilot left out thinking disabled | Pilot has three arms, including thinking disabled at effort `low`, chosen on spot-check accuracy and self-consistency (4.3). |
| 16 | minor | Appendix C numbers did not reproduce; denominators differed | One script in the repo, `docs/ml/model_a_numbers.py`, recomputes every number from `data/silver` and `data/gold`. Single denominator of 9,989 facilities for grades. Corrected values: fragility 294 (66%), 401 (39%), 961 (47%); EmOC none 5,307; claims wording 33.5% and 35.5% under the script's stated pattern (the v1 draft's 37% and 44% could not be reproduced); meta-statements 874; photo captions 436 under a stricter pattern. |
| 17 | minor | Gold depended on an unapproved taxonomy; data/labels was empty | Gold now exists (429 texts, written 15:15, v0 rules, strata and weights recorded in `labels/gold_sample.csv`). v1 is frozen as the working definition and aligned with the gold rules wherever grades allow, leaving about 30 rows to reconcile into `labels/gold_facts_v1.csv` (section 3, Appendix D). Gold and agent labels require the 9 grade tags plus `nicu`, `h24` and `negated`; noise tags are optional. |
