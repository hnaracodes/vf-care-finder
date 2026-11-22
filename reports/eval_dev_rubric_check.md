# Dev rubric check: do Q1 "yes" votes rest only on a specialty listing?

Decision D051 item 2. Dev half only. The sealed half was not touched, run or read.

## Threshold, fixed before any judging in this run

Written at 04:43 UTC on 22 Nov 2026, before the dev judge run below.

- Population: every Q1 unit on the dev half whose final verdict (A and B agree, or the majority of A, B and C) is yes. If there are more than 120, a random 120 (seed 20261122).
- Each is read by hand (by the agent) using the quote and reason of every judge who said yes, checked against the record.
- Class "listing-only": the yes rests only on a specialty or department name (a "Specialties listed" entry, a department list, a doctor's specialty or qualification), with no fact that names the service being performed or available.
- Class "service named": at least one yes judge cites text that names the service itself as performed or available at this facility (for example "LSCS", "caesarean section", "dialysis unit", "haemodialysis machines", "CT scan").
- Listing-only votes are "common" if more than 10% of the final-verdict yes answers are listing-only. Above 10%: write rubric j2 and test it. At or below 10%: change nothing in the rubric.
- The share is reported with a 95% Wilson interval. The decision uses the point estimate against the 10% line, as fixed here.

## Result

**Listing-only yes votes are not common on the dev half: 8 of 107 final yes verdicts (7.5%; 95% Wilson interval 3.8% to 14.1%) rest only on a specialty or department name.** That is under the 10% line, so the Q1 rubric stays j1 and no amendment was written or sealed. Caveat: the interval's upper end is above 10%, so the sealed half could show a higher share; the point estimate decides, as fixed above.

Sensitivity of the count to my two judgment calls:

- Counting #59 too (both yes judges quoted specialty lines, but judge B's reason also cites the record's "Spinal Surgery" procedure fact): 9 of 107 (8.4%; 4.5% to 15.2%).
- Moving the three departments or specialties named after the service itself ("Renalcare & Dialysis" centre, "Nephrology & Dialysis department", "Specializes in ... Dialysis") to "service named": 5 of 107 (4.7%; 2.0% to 10.5%).

Neither reading crosses 10%.

## The run

- Command: `python scripts/eval_sealed.py judge --split dev --cap-usd 4.93` (a 20-call check first, then the full half). The cap is cumulative: $0.1024 already in the eval ledger plus the $4.83 hard stop.
- All 140 dev queries were fully judged: 615 units (414 Q1, 201 Q2). 85 units (14%) needed judge C, and none was left unresolved. That made 1,287 API calls in this run, plus 48 cache hits.
- Spend this run: $4.5631 (judge_dev ledger total $4.6596 over 1,315 calls; eval ledger total $4.6655, including two pre-seal model checks). This is above the $3.76 estimate and under the $4.83 hard stop. The estimate assumed 7.7% disagreements (measured 14%) and half the possible walk.
- The sealed half was not touched, run or read.

## Classification

Every final yes was read: the quote and reason of each yes judge, and, for every candidate listing-only case, the record's lines about the service.

| Class | Count | Share |
| --- | --- | --- |
| Service named in a fact (performed, offered, or a unit or equipment that provides it) | 99 | 92.5% |
| Listing-only (specialty, department or specialization name only) | 8 | 7.5% |

Listing-only, by tool that picked the unit: specialty filter 4 units (40, 44, 48, 79), any-fact 3 (17, 44, 48), ranker 1 (29), walk only 2 (15, 27). Without them, top-pick precision on dev is: specialty 25 of 112 (22%; 16% to 31%), any fact 69 of 83 (83%; 74% to 90%), ranker 76 of 82 (93%; 85% to 97%). The harness's own figures with j1 are 29, 72 and 77 yes.

Listing-only examples (5 of 8):

1. #15 burns, Aabhari Plastic Surgery Centre (walk). Both judges quoted "Specializations include Burns Management, General Medicine, Obstetrics & Gynaecology, ..." The record has no burns unit, ward or care fact.
2. #29 burns, RJ Super Speciality Hospital (ranker pick). Both judges quoted "Specialization in Burns Management", the only burns line in the record.
3. #40 cardiac surgery, Manipal Goa Cancer & General Hospital (specialty pick). B and C quoted "Centre of Excellence for Cardiothoracic Vascular Surgery". No fact names CABG, valve or open-heart surgery.
4. #48 dialysis, Navjeevan Nursing Home (specialty and any-fact pick). Both judges quoted "Specializes in Orthopedics ..., General Medicine, Dialysis, Critical Care, and Physiotherapy". No fact says dialysis is performed or that machines exist.
5. #79 neurosurgery, Jeevan Rekha Hospital (specialty pick). A quoted the "Specialties listed" line and B a department list ("... Anaesthesiology, Neurosurgery, Dentistry ..."). No neurosurgical procedure is named.

The other three are #17 (dialysis, "Centres of Excellence include ... Renalcare & Dialysis ..."), #27 (neurosurgery, "Offers surgical care across over 19 specialties including ... Neurosurgery ..."; the only spine line is a visiting orthopaedic spine surgeon) and #44 (dialysis, "Nephrology & Dialysis department is available").

Service-named examples (5 of 99):

1. #2 emoc: "Caesarean section is performed" (both judges).
2. #10 dialysis: "Hemodialysis is offered with modern dialysis machines." (both).
3. #53 burns: "Burns care includes specialized treatment with an eight-bed Burns Unit and use of early excision and grafting techniques" (both).
4. #76 ct: "Siemens SOMATOM go.Up CT scanner" (both).
5. #78 neurosurgery: "Brain tumor surgery is performed" (A) and "Neurosurgery for brain tumors; microsurgery for lumbar and cervical disc prolapse; spinal tumors; head and spine injuries" (B).

Borderline calls, recorded so they can be checked: I classed as service named the statements of provision that use a discipline's name ("Neurosurgery is provided", "Provides advanced neurosurgical treatment", "Provides cardiology and cardiothoracic surgery services", "Offers radiation oncology services", "Offers nephrology and transplant services including ... Dialysis ..."), and "24x7 Accident and Emergency Care" for trauma. A stricter reader could move some of these to listing-only.

## A second finding: the harness's automatic listing-only flag undercounts

Amendment 1's report-only "listing-only" figure looks only for quotes from the record's "Specialties listed" line. On these 107 yes verdicts it flags 1 strict (#79) and 2 loose (#59, #79), against 8 by hand. Most listing-only votes quote specialty or department lists inside capability facts ("Specializations include ...", "Centres of Excellence include ..."), which that rule cannot see. So the "precision without listing-only yes votes" rows in `results_dev.md` are close to the plain precision, and that understates the issue. The rows are labelled as the Specialties-line reading, so they are not wrong, but no reader should treat them as a full count. Changing the rule now would be an amendment to what is reported, so I left it as it is. If the orchestrator wants the broader reading on the sealed half, it can be added as a further report-only amendment before sealing.

## Other dev numbers worth knowing (dev half, not the result)

- Ranker skipped a nearer judge-supported option in 23 of 82 answers (28%; 19% to 39%). In 22 of these the nearest supported record has no WP11 major flag. This is the headline "cost of caution" figure, and it is larger than the dry run suggested.
- Ranker abstained with a supported option in the radius in 1 of 58 abstentions (2%).
- `estimate --split dev` now gives about $4.55 expected for a half ($3.01 to $5.50). The sealed half under the km radii should cost about that. D050 reserved about $5 for it, which is below the high case.

## Files

- Per-unit classification: `data/labels/eval_sealed/dev_rubric_check_q1_yes.csv` (107 rows: unit id, votes, class, borderline note, yes quotes).
- Dev judging outputs, refreshed by this run: `judged_dev.json`, `judgments_dev.csv`, `outcomes_dev.csv`, `results_dev.json`, `results_dev.md`, `cost_estimate_dev.json` and `spend.jsonl`, all in `data/labels/eval_sealed/`.

## Harness changes (no change to the active rubric)

- Prompt versions are now set per question (`KIND_PROMPT_VERSION`). Cache keys and folders use them, so a later Q1 rubric would not throw away cached Q2 answers. j1 keys, folders and the prompt sha256 (`7fba0ba0...`) are unchanged, and a test pins this.
- `SYSTEM_Q1_J2` is kept as an inactive candidate, together with a dev-only `rejudge` subcommand (`rejudge --split dev --prompt-q1 j2 --units-file ids.txt`), in case the orchestrator wants j2 tested anyway. It refuses the sealed half.
- `EXPECTED_SEALS` and the protocol are unchanged.
