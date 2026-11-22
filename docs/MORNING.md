# Morning, Nov 22: what to do before 12:30 PM

Written 10:15 ET. Repo is clean on branch `rescope` at `8072cbb`. Nothing has been pushed or submitted. Project LLM spend is $18.25 (ledgers under `data/labels/`).

## 1. The app (2 minutes)

URL: https://vf-care-finder-7474645226493940.aws.databricksapps.com (workspace login only, per D057).

Last deploy: deployment `01f1e65cfe49190ea3a7672a51f4b564`, SUCCEEDED at 05:11 ET (10:11 UTC) Nov 22. Free Edition stops the app 24 hours after that, so about 05:11 ET Nov 23. At 10:15 ET it was RUNNING, compute ACTIVE, `/api/health` ok (bundle mode, 9,989 facilities, recipe v0.3).

Check it:

```bash
databricks apps get vf-care-finder -o json | jq '{app_status, compute_status}'   # want RUNNING and ACTIVE
curl -s -H "Authorization: Bearer $(databricks auth token -o json | jq -r .access_token)" \
  https://vf-care-finder-7474645226493940.aws.databricksapps.com/api/health | jq '{ok, mode, n_facilities}'
```

Restart it just before recording (D034; it also resets the 24-hour clock):

```bash
databricks apps stop vf-care-finder && databricks apps start vf-care-finder
```

In the UI: workspace left sidebar, Compute, then the Apps tab (some workspaces show Apps directly in the sidebar), click `vf-care-finder`, then Stop, then Start. Logs: `databricks apps logs vf-care-finder`. More: `dbapp/README.md`, "Restart before judging".

## 2. Label the 40 sealed items (about 15 minutes, optional but valuable)

Instructions: `reports/HUMAN_LABELING.md`. File: `data/labels/eval_sealed/human_labels_sealed.csv` (40 rows, all blank). Do not open `human_key_sealed.csv` first. Then:

```bash
.venv/bin/python scripts/eval_sealed.py agreement --split sealed
.venv/bin/python scripts/wp9_findings.py      # rebuild findings with the agreement figure
```

If you do this, add one footer line to beat 7 of the demo script. If you skip it, the write-ups already say the human check is pending.

## 3. Spot-check outside-list lines in the demo (about 20 minutes)

Source PDF: https://qps.nhsrcindia.org/sites/default/files/2026-04/LaQshya_Master_data-08-04-2026.pdf ("row" is the S.No. column). All were read by an AI agent only.

**Must check (filmed in beat 5 and cut beat 3).** Record "District Hospital, Nashik" (`6720cccc-...`), shown in "Why not the nearer ones?" for Nashik:
"Government list (LaQshya, NHSRC, 8 Apr 2026, page 35 row 1109): maternity operating theatre certified, valid to 31 Dec 2026."
Confirm row 1109 is District Hospital Nashik, Maternity OT, certified, valid to 31 Dec 2026 (row 1108 should be its Labour Room). If it does not hold, cut the government line from beat 5 and cut beat 3 (demo script step 7).

**Check if they appear in a filmed frame.** The "Also on government lists nearby" lane, not linked to any record, shows these for the demo searches:

| Search | Listed facility | Page, row | Says |
| --- | --- | --- | --- |
| PIN 226001 | Ram Sagar Misra Hospital, Lucknow | 64, 2012 | Maternity OT certified, valid to 5 Jul 2025 |
| PIN 226001 | Jhalkari Bai Mahila Hospital, Lucknow | 63, 2001 | certified, valid to 31 Aug 2025 |
| PIN 226001 | CHC Sarojini Nagar, Lucknow | 61, 1922 | conditionally certified, valid to 13 Mar 2024 |
| Nashik | General Hospital Malegaon | 35, 1116 | certified, valid to 1 Mar 2023 |
| Nashik | SDH Kalwan | 37, 1174 | certified, valid to 1 Mar 2023 |
| Nashik | SDH Manmad | 38, 1192 | certified, valid to 3 May 2022 |
| Gadchiroli | SDH Aheri | 36, 1139 | conditionally certified, valid to 10 Oct 2020 |
| Gadchiroli | Women Hospital Gadchiroli | 39, 1235 | certified, valid to 10 Oct 2022 |
| Gadchiroli | SDH Armori | 36, 1144 | conditionally certified, valid to 1 Mar 2021 |

The app words the lapsed ones "valid to <date>, not renewed on that list". Jokihat dialysis shows no outside lines.

## 4. Record and submit (by 12:30 PM ET)

1. Read `reports/demo_script.md`, "Before any take" first (`?demo=1`, Demo chip showing, decide on card names). Use the 90-second cut if short on time.
2. Retest notes: in beat 6 press Play once before "Add here" (an edit before Play shows no motion). Search `Nashik`, not PIN 422001 (about 69 km wide).
3. Code: there is no git remote. Create a GitHub repo, check `git ls-files data/` shows only `data/README.md`, `data/raw/MANIFEST.json`, `data/labels/gold_facts.csv` and `.gitkeep` files, then `git remote add origin <url> && git push -u origin rescope:main` (main is an ancestor, so this is clean). Decide public or private first: the repo holds facility names and quotes (`gold_facts.csv`; answer JSON in `app/design/`, `app/src/fixtures/`) from a table with unchecked reuse terms. If the form wants a file: `git archive --format=zip -o vf-care-finder.zip HEAD`.
4. Analysis: `README.md` (with "What we propose") and `reports/methods_and_limits.md`; full evaluation in `reports/eval_sealed_results.md`.
5. Submit code, analysis and video at https://forms.gle/jSauvMcZqT1pyqaN7. The 12:30 PM deadline and its time zone are from D047, which calls them tentative: confirm on the form. Judging is 1 to 4 PM.

## 5. What did not work or is weak

- **Not better than the simple baseline:** 89% vs 84% top-pick precision against "any fact names it" (p = 0.125). The case rests on the gap over the specialty filter (20%). `reports/eval_sealed_results.md`.
- **34 of 101 sealed picks judged not sensible for the place,** mostly merged or multi-branch records; the judges agree poorly there (kappa 0.44). 4 nearer supported facilities were graded 1 by recipe v0.3 (grading misses). Same file.
- **v1 ranker not proven better than v0** (10 vs 4, 12 ties, p = 0.18), on the scenarios used to fix it. `reports/ranking_v0_vs_v1.md`.
- **Tagger:** ICU shipped on a weak 15 of 25 read; round 2 changed no recommendation. `reports/model_a_results.md`.
- **Dental-theatre miss is hidden, not fixed** (demo script beat 9).
- **No human checks yet:** gold set, reviews, link checks and judges are all AI. AERB links pass the 0.90 bar only with unsure links left out (0.879 otherwise).
- **Process:** froze at 05:15 ET, not 04:00 (Amendment 3); the notebook ran an earlier build; no Databricks job ran.
- **App:** `/api/simulate` off by design (`/api/sim` works); Jaipur dialysis card 3 is a children's hospital (D061); phone layout bugs in the why-not heading and what-if banner. Details: `reports/overnight_test_and_fix.md`, `reports/methods_and_limits.md`.

## 6. Key numbers (all from `reports/findings.json`)

- 9,989 facility records, 88% private.
- 4,489 list obstetrics; 418 of them name a caesarean (432 records name one in all); 373 of those 432 say nothing about blood.
- Post-office points within 50 km straight-line: 81.5% of an obstetrics listing, 40.2% of a record naming the operation.
- Sealed test, 140 queries at 60 random post offices. Top pick clearly shows the service: specialty filter 20%, any fact 84%, our ranker 89% (82% to 94%). Answered: 82%, 72%, 72%.
- Cost of caution: abstained on 39; judges found a supported option missed in 0 of 39 (31 had no signal nearby). Skipped a nearer supported record in 16% of answers.
- Judges agree on "shows the service" (kappa 0.87), not on "sensible" (0.44).
- Tagger macro F1: 0.916 shipped mix vs 0.890 rules alone, on 777 agent-labeled facts.
- Trust checks: 2,471 records flagged (25%); 1,010 of 10,957 grade 2+ lines blocked. Resolver 54 of 55 test cases.
- LaQshya: 63 agent-confirmed links; the automatic matcher alone was right 55% of the time.
