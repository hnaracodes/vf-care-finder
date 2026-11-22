# Outside data for "find the right care": final acquisition plan

**Finding.** In a 12-hour run, outside lists can give independent, service-level labels for roughly 1,500 to 2,300 of our 9,989 facilities; a 5-hour run gets roughly 1,100 to 1,500. These are estimates from 40-row pilots and automated matches, and the overlap between sources was guessed, not measured.

The gap search changed three things:

- **Priority-state private hospitals.** It found the first label sources that reach small private hospitals in the priority states: the PM-JAY empanelment export (7,737 rows across UP, Bihar, Rajasthan and Maharashtra) and Rajasthan's RGHS list, whose rules tie cardiology to a cath lab and nephrology to dialysis.
- **NICU level and ventilator ICU.** It found signals for both: IAP Neonatology Chapter, PICC paediatric ICUs, NBEMS emergency-medicine criteria, and the transplant registers.
- **Negatives.** It found explicit exclusions in CGHS city lists and dated public-hospital negatives in district monitoring reports.

The caveat stands. Labels are mostly positive and concentrate on large accredited, teaching and insured hospitals. Hard negatives exist at scale only for radiotherapy, and Bihar stays thin. Open-web checking is not a label source: it gave 2 strict labels in 20 pairs.

## Ranked shortlist (top 14 of 26)

| # | Source | Main lines | Pilot match | Label meaning | Terms | Agent-h |
|---|---|---|---|---|---|---|
| 1 | NABH scope certificates (1,118 valid) | 11 lines, not trauma or burns | 21/40, 2 maybe | on-site accreditation, dated, explicit exclusions | medium | 12 |
| 2 | AERB radiotherapy licences (727) | radiotherapy | 9/40; 163 auto | mandatory licence, absence meaningful | low | 4 |
| 3 | NMC course register (911 institutions) | cath lab, CTVS, neuro, nephro, neonatology, critical care, oncology | 20/40, 4 maybe | regulator permission | medium | 7 |
| 4 | MCC NEET-SS DM/MCh and DrNB | same, plus 550 mostly private hospitals | 19/40, 5 maybe | training seats | medium | 6 |
| 5 | PM-JAY HEM export, 4 priority states (re-host) | cath lab, CTVS, neuro, polytrauma, burns, oncology | 5/40, 5 maybe; 407 auto | specialty empanelment, private rows only | medium | 8 |
| 6 | RGHS Rajasthan (749) | cath lab, CTVS, dialysis, neuro, oncology | 7/40, 1 maybe; 83 auto | empanelment tied to equipment rules | low | 4.5 |
| 7 | MJPJAY Maharashtra (2,237) | 10 lines, private rows | about 120 linked | empanelment | medium | 5 |
| 8 | MCC NEET-PG DNB, split by stream | emoc, CT, MRI, ventilator ICU, radiotherapy | 9/40, 5 maybe | training seats; reaches district hospitals | medium | 7 |
| 9 | NBEMS rulebook and department lists | turns 4 and 8 into labels | 14/39 | inspected minimum criteria | medium | 4 |
| 10 | CGHS city lists (Patna, Kanpur, Lucknow, Meerut, Prayagraj, Pune) | 10 lines with explicit exclusions | Patna 19/40; 2018 lists 12/40 | payer scope after NABH or QCI inspection | low | 7 |
| 11 | IAP Neonatology and PICC paediatric ICUs | NICU level III, PICU ventilation | 16/40; 19/40 | society inspection | medium | 4.5 |
| 12 | DGEHS Delhi NCR (500) | 9 lines | 14/40, 9 maybe | empanelment | low | 4 |
| 13 | CMCHIS Tamil Nadu (1,009 private) | 9 lines, true and false flags | 4/25; reverse 11/20 | empanelment from hospital documents | medium | 4 |
| 14 | ZTCC Pune and Nagpur transplant registers | ICU, cardiac surgery | 10/40; 12/25 | THOTA-linked registration | low | 3.5 |

**Also in the 12-hour plan:**
- NCDIR cancer registries, e-RaktKosh, the 196 trauma facilities and NABI burn units.
- RajCovidInfo ventilator beds, with 53 positives on our Rajasthan rows.
- District monitoring reports (PRC), for dated public negatives.
- NCG and PIB cancer lists, CAG audits, and the Esri NIN public-facility spine.

**Only if agents free up:** MusQan, IAEA DIRAC, and one copy of the 2017 blood bank directory.

**Closed to agents:**
- PM-JAY portal: geo-block and CAPTCHA. The re-hosted export stands in.
- AERB eLORA (CT and cath lab licences) and NOTTO: unreachable.
- PMNDP centres: login only.
- ABDM HFR, HMIS, and the NQAS host (its certificate has expired).
- NNF, Manyata and staffnews.in: robots.txt blocks agents.
- ISCCM: needs written consent.
- UP SECTS: needs postbacks, and it would only repeat the HEM export.

About 40 low-relevance leads were not verified and are not in the plan.

## Coverage by service line

| Line | Strongest independent sources | Hard negatives | Weak spot |
|---|---|---|---|
| emoc | LaQshya, NABH OBG, DNB OBG (NBEMS stream) | about 8 explicit exclusions; PRC 'no C-section' | nothing certifies private 24x7 C-section |
| nicu | IAP level III, NABH, DrNB/DM neonatology | PRC 'NICU No' at public DHs | no graded list in UP or Bihar |
| dialysis | NABH, RGHS, DrNB/DM nephrology, CGHS, CMCHIS | about 10 CGHS 'excluding dialysis'; PRC; CAG | public PMNDP list behind a login |
| icu | DNB Emergency Medicine, PICC, ZTCC, NABH, RajCovidInfo | CAG and PRC 'No ICU' (few link) | adult ventilator ICUs at private hospitals |
| cardiac_surgery | NABH, MCh/DrNB CTVS, ZTCC heart, HEM, RGHS | CGHS 'except CABG' | public non-teaching hospitals |
| cath_lab | NABH, RGHS, CGHS 'invasive', HEM, DrNB cardiology | about 10 'non-invasive' qualifiers | AERB eLORA unreachable |
| mri | NABH, DNB Radio-diagnosis (NBEMS stream) | 3 CAG; PRC Bihar | no regulator list |
| ct | NABH, DNB Radio-diagnosis, CMCHIS, DGEHS | 12 CAG Rajasthan | AERB eLORA unreachable |
| trauma | 196 trauma facilities, HEM polytrauma, MJPJAY | soft only | no regulator or accreditor |
| burns | NABI, HEM BM, MJPJAY, CGHS | soft only | thinnest line |
| neurosurgery | NABH, MCh/DrNB, HEM, RGHS, CMCHIS, CGHS | soft only | small private claims unlabeled |
| oncology | AERB, HBCR, NCG, NABH, HEM, RGHS | radiotherapy only | no chemotherapy negatives |
| blood_bank | e-RaktKosh, 2017 directory, NABH | 7 CAG; PRC | no live licence register |

## How labels are built

**Linking.** Only confident links carry labels, and a label attaches to a building, never to a parent college, chain or co-located unit. A label is dropped where our record already cites the same list, for example the 592 records that cite PM-JAY aggregators.

**Assessor families.** Correlated lists count as one vote: NABH with CGHS, NMC with MCC and NBEMS, and HEM with MJPJAY.

**Positive tiers**, strongest first:
1. Regulator licence.
2. Accreditation or a training permission whose criteria require the service in-house.
3. Private-hospital empanelment, and rules that allow an MoU or rotation.
4. Membership, self-report and dated field observation.

**Negatives.**
- Hard negatives come only from explicit exclusions, audit and visit statements with a quote, and AERB absence for radiotherapy.
- A list's silence counts as a soft negative only where that list would name the service, and only if the list names it for at least 85% of facilities that have a strong positive from another family.
- Everything else is 'no evidence of'.

**Model.** It uses record features only. Cross-validation is grouped by cluster, chain and known duplicates, and leave-one-family-out checks test whether it learned the service rather than the source. The rule grade is the baseline.

## Plan and time

**12-hour plan.** 15 agents and about 130 agent-hours. The critical path is about 10 hours, set by the NABH download and linking. Because the agent pool runs at about 87%, the realistic finish is 10.5 to 11.5 hours. If the run is behind at hour 5, drop CAG first, then NCG/PIB, the Esri NIN extras, the PRC reports, RajCovidInfo and CMCHIS.

**5-hour cut.** It keeps:
- NABH, currently valid certificates only
- AERB, MCC NEET-SS, the HEM export for the four priority states, RGHS, MJPJAY and DGEHS
- IAP and PICC
- HBCR, NABI, the trauma list, ZTCC, e-RaktKosh and CGHS Patna

It fits one pooled model.

## What can go wrong

- **Linking.** Linking is the bottleneck. Pilot confident rates ran from 0% to 52%, and our own table has wrong PINs, conflated records and duplicates. A 30-pair audit measures link precision before training.
- **Label meaning.** Several big sources record a billing permission or a training seat, not a 24x7 service. HEM's column may list specialties applied for rather than approved.
- **Terms.** Most lists restrict republication. The app and the video should show evidence badges, not the tables.
- **Weak lines.** Trauma, burns, MRI and adult ventilator ICUs may end up with too few labels. If so, report that rather than pad them with proxies.
- **Baseline.** The model may not beat the rule grade. If so, say it and ship the grade with evidence badges.

## Decisions needed from Hruday

1. Approve internal-only use of restricted lists, with badges in public outputs.
2. Approve the bulk pulls, all at 1 request per second or slower: NMC, the NABH certificates, the CMCHIS export, e-RaktKosh, Esri NIN, the PM-JAY statewide PDFs and the Wayback CGHS lists.
3. Confirm that Wayback copies of formerly public documents are acceptable, and that third-party re-hosts are acceptable: apniyojana for HEM, sarkariyojana for MJPJAY, and medicaldialogues for the NBEMS lists.
4. staffnews.in blocks Claude agents. If you want CGHS Nagpur 2025 or Jaipur 2018, download them yourself; otherwise they are skipped.
5. Pick the 12-hour plan or the 5-hour cut.
6. Decide whether HOT OSM stays a clean holdout once government-directory facilities are added.
7. Optional, and unlikely before Nov 22: someone on an Indian network could open AERB eLORA, NOTTO and the PM-JAY portal, and consent emails would open ISCCM, NNF, Manyata and the PMNDP centre list.