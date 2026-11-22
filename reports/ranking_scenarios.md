# Ranking scenarios: hand check

Judged run: `reports/ranking_scenarios_raw.json`, created 2026-11-22T03:49:50Z by `scripts/wp5_scenarios.py` at commit `8ff71cb` with uncommitted code. Recipe v0.2, grades from `student:s1-1b[ob_delivery,h24,icu,ventilator]+regex:v0.2[rest]`, flags `wp11_v2`, score mode v0 (provisional, D039).

## Findings

- Of the 48 recommendation cards shown, 31 are right (65%), 16 defensible (33%) and 1 wrong (2%).
- All 10 answers that showed no cards were judged right: 7 "no reliable option", 2 "pick a location" and 1 "location not found". abstention_right 10, abstention_wrong 0.
- By scenario, judged on the weakest card: 13 of 26 have nothing below right, 12 have at least one defensible card and none wrong, 1 has a wrong card.
- Abstention rate: 7 of the 23 scenarios whose place resolved returned "no reliable option" (30.4%, the run's own figure). Over all 26 scenarios that is 26.9%. Recipe queries only: 6 of 21 (28.6%). Counting the 3 location prompts as well, 10 of 26 answers (38.5%) showed no card.
- All 4 scenarios written to expect abstention did abstain. 3 scenarios expected an answer and abstained instead (`brief_dialysis_rural_bihar`, `fallback_antivenom_raipur`, `disambiguated_aurangabad_bihar`). The judges checked the records around each and found no facility within the radius whose facts name the service, so they marked all three abstentions right. The written expectation was wrong for this data, not the ranker.
- The weakness is the third card, more than the first. Most defensible marks are a slot that a better-evidenced or closer facility should have taken, not a facility that lacks the service. The patterns are listed below.

Caveat: the marks come from independent judges reading the quoted facts and the full facility records. They are one reading per card, not a consensus; one card shows up in two scenarios and got two different marks (see below).

## Totals

| Mark | Count | Share |
|---|---:|---:|
| right (cards) | 31 | 65% of 48 cards |
| defensible (cards) | 16 | 33% of 48 cards |
| wrong (cards) | 1 | 2% of 48 cards |
| abstention_right | 10 | 100% of 10 no-card answers |
| abstention_wrong | 0 | 0% of 10 no-card answers |
| total judgments | 58 | |

By method:

| Method | Cards | right | defensible | wrong |
|---|---:|---:|---:|---:|
| recipe | 45 | 29 | 15 | 1 |
| keyword | 3 | 2 | 1 | 0 |

By card position:

| Rank | right | defensible | wrong |
|---:|---:|---:|---:|
| 1 | 13 | 3 | 0 |
| 2 | 10 | 5 | 1 |
| 3 | 8 | 8 | 0 |

By scenario (weakest mark among its cards):

| Scenario | Service | Place | Status | Marks |
|---|---|---|---|---|
| `brief_dialysis_jaipur` | Dialysis | Jaipur (Rajasthan) | ok | right, right, right |
| `brief_csection_lucknow` | Emergency C-section and high-risk delivery | Lucknow (Uttar Pradesh) | ok | right, defensible, right |
| `brief_dialysis_rural_bihar` | Dialysis | Jokihat (Bihar) | no_reliable_option | abstention_right |
| `brief_highrisk_maharashtra` | Emergency C-section and high-risk delivery | Nashik (Maharashtra) | ok | right, defensible, defensible |
| `brief_trauma_highway` | Trauma and accident care | Kotputli (Rajasthan) | ok | right, right, right |
| `line_nicu_pune` | Newborn intensive care (NICU) | Pune (Maharashtra) | ok | right, wrong, defensible |
| `line_icu_patna` | Intensive care with ventilator | Patna (Bihar) | ok | right, right, defensible |
| `line_cardiac_bhubaneswar` | Heart surgery (bypass, valve) | Bhubaneswar (Odisha) | ok | defensible, right, defensible |
| `line_cathlab_indore` | Heart attack care (angiography, angioplasty) | Indore (Madhya Pradesh) | ok | right, right, defensible |
| `line_mri_coimbatore` | MRI scan | Coimbatore (Tamil Nadu) | ok | right, right, right |
| `line_ct_guwahati` | CT scan | Guwahati (Assam) | ok | defensible, right, right |
| `line_burns_nagpur` | Burn care | Nagpur (Maharashtra) | ok | right, defensible, right |
| `line_neuro_chandigarh` | Head injury and brain surgery | Chandigarh (Chandigarh) | ok | right, right, defensible |
| `line_oncology_ahmedabad` | Cancer treatment (chemotherapy, radiotherapy) | Ahmedabad (Gujarat) | ok | right, defensible, right |
| `remote_dialysis_leh` | Dialysis | Leh (Ladakh) | no_reliable_option | abstention_right |
| `remote_emoc_tawang` | Emergency C-section and high-risk delivery | Tawang (Arunachal Pradesh) | no_reliable_option | abstention_right |
| `remote_cardiac_portblair` | Heart surgery (bypass, valve) | Port Blair (Andaman and Nicobar Islands) | no_reliable_option | abstention_right |
| `remote_emoc_gadchiroli` | Emergency C-section and high-risk delivery | Gadchiroli (Maharashtra) | no_reliable_option | abstention_right |
| `fallback_cataract_hyderabad` | Keyword search: cataract surgery | Hyderabad (Telangana) | ok | defensible, right, right |
| `fallback_antivenom_raipur` | Keyword search: snake bite anti venom | Raipur (Chhattisgarh) | no_reliable_option | abstention_right |
| `ambiguous_aurangabad` | Emergency C-section and high-risk delivery | Aurangabad | needs_location_choice | abstention_right |
| `ambiguous_bilaspur` | Dialysis | Bilaspur | needs_location_choice | abstention_right |
| `disambiguated_aurangabad_bihar` | Emergency C-section and high-risk delivery | Aurangabad, Bihar | no_reliable_option | abstention_right |
| `pin_dialysis_302001` | Dialysis | 302001 | ok | right, right, defensible |
| `coords_icu_lucknow` | Intensive care with ventilator | 26.85, 80.95 | ok | right, defensible, defensible |
| `nonsense_place` | Emergency C-section and high-risk delivery | Xqzvillepur | location_not_found | abstention_right |

## The wrong card

**`line_nicu_pune`, card 2: Dhanwantari Hospital** (Pune, 1.1 km, grade 3, medium confidence).

Dhanwantari Hospital is a badly merged record of 10+ different 'Dhanvantari' hospitals: Chalisgaon (its PIN 424101, 246 km from its map point), Saravali/Thane, Neral/Karjat, Matunga, Navi Mumbai, Panvel, Ahmednagar, Paithan, Nashik and Nigdi. The NICU facts ('NICU with 3 phototherapy machines', '12 cots', 'PICU with 2 ventilators') cannot be tied to the Pune point 1.1 km from the centre. The ventilator is a PICU ventilator, not a NICU one. The possible_merge flag is graded only 'minor', which is too lenient. The record does not establish a NICU at this location.

Why it got through: the record's NICU facts are real text, so the grader gave grade 3. WP11 did raise `possible_merge`, but at severity `minor`, and the card was shown at medium confidence. A record that names ten or more same-name hospitals in different towns needs a stronger flag, or a block, before its facts can be placed at one map point.

## Patterns behind the defensible marks

Grouped from the judges' notes. A card can sit in more than one group. These describe the behaviour; they are not a request to make any named facility win (WP5 pitfall: weights change only by reading the output as a whole).

- **A corroborated record far away outranks supported records nearby.** The v0 score gives evidence weight 0.5 and distance 0.3, so grade 3 far away can beat grade 2 close by. `brief_highrisk_maharashtra` card 3 (Currae, 135 km against a 50 km radius, with three grade 2 C-section facilities at 12.7 to 20.7 km), `line_cathlab_indore` card 3 (Bhopal, 168 km against a 100 km radius, with grade 2 cath labs inside Indore), `line_cardiac_bhubaneswar` card 3 (Rourkela, 242 km, inside the 250 km heart-surgery radius, with supported options at 6.6 to 22 km). The first two cards say they are beyond the usual radius.
- **Pediatric-only critical care fills a generic ICU slot.** `line_icu_patna` card 3 (Patna Children Hospital) and `coords_icu_lucknow` cards 2 and 3 (JPS Children Hospital; SGPGIMS, whose only ICU evidence is one pediatric case report). Closer general ICUs with strong evidence were passed over. Related quote problems: `line_icu_patna` card 1, `coords_icu_lucknow` card 1 and `line_neuro_chandigarh` card 1 quote a NICU or PICU fact for an adult requirement while better quotes exist in the same record.
- **Merged or duplicate records are not caught, or are flagged too lightly.** `line_nicu_pune` card 2 (the wrong card), `line_neuro_chandigarh` card 3 (Alchemist mixed with Ojas Hospital and the Gurugram branch; only a city-mismatch flag shown), `line_oncology_ahmedabad` card 2 (HCG Aastha likely duplicates card 1: identical coordinates and equipment list; the duplicate check did not pair them). The beyond-radius card in `remote_emoc_gadchiroli` quotes a trial protocol line from another hospital and carries no merge flag.
- **Grade 3 from list matching or a single fact.** `line_cardiac_bhubaneswar` card 1 (one department-level statement, no named procedure) ranks above AIIMS, whose record names CABG, valve surgery and TAVR but is grade 2 for lack of a blood bank fact. `line_nicu_pune` card 3 (NICU evidence is a milk bank 'near NICU'; ventilator evidence is adult), `line_burns_nagpur` card 2 (a combined orthopaedics and burns department), `pin_dialysis_302001` card 3 (one fact). In Nagpur the judge also found AIIMS graded 1 for department-list evidence similar to what earned Mure Memorial grade 3.
- **Thin grade 2 records take slots from better-documented grade 2 records.** `brief_highrisk_maharashtra` card 2 (17 facts, 2 domains, no OT or anaesthesia fact) and `brief_csection_lucknow` card 2 (no blood bank, OT or anaesthesia fact). Elite scored 0.767 and Krishna Medical Centre 0.780, but scores within 0.02 count as a tie and the tie goes to the closer one, 5.1 km against 5.2 km, so the lower score is listed first.
- **Service fit.** `line_ct_guwahati` card 1 is a cancer institute whose CT facts are all about staging, for a query that is often an emergency need.
- **Keyword fallback breaks ties by distance only.** `fallback_cataract_hyderabad` card 1 is a small Lasik clinic with one cataract fact, ahead of an eye centre with 70,000+ cataract surgeries. Keyword matches are capped at low confidence, which the card says.

Smaller label issues the judges noted: the Bagul Nursing Home location note says 'consistent with its PIN code' at 20.2 km from it; Hamidia Hospital is labelled private but its record says government-run; the Raiganj beyond-radius card in `brief_dialysis_rural_bihar` shows the state as 'Uttar Dinajpur'; beyond-radius cards in `remote_dialysis_leh` and `remote_emoc_gadchiroli` quote weaker facts than the record holds; Monilek's 'low' confidence undersells a 14-domain record.

## Judge disagreement

Monilek Hospital (`acdc2105-2e99-4c66-92c7-6d1f6c932ce7`) is card 3 in both `brief_dialysis_jaipur` (marked right) and `pin_dialysis_302001` (marked defensible). Same record, same rank, two judges. Both marks are kept as given; the totals count each card once per scenario.

## Every scenario

### `brief_dialysis_jaipur`

- Query: Dialysis (`dialysis`), place "Jaipur, state hint Rajasthan". Method recipe, radius 50 km.
- Expected `ok`, got `ok`. Why expected: Brief query 1. State capital with many private hospitals; several should name dialysis in their facts.
- Location: Centre of Jaipur district, Rajasthan, about 144 km across. Matched the district name 'JAIPUR' exactly in Rajasthan. This is the district centre, not an exact location.
- Answer: 3 of 21 recommendable facilities within 150 km, best first.

1. **Soni Hospital** (Jaipur, Rajasthan; private hospital), 3.4 km, grade 3, high confidence, evidence strong.
   Quoted: "Dialysis center performing over 400 dialysis cases per month".
   Mark: **right**. Soni Hospital, Jaipur, 3.4 km. Strong record: 'Dialysis center performing over 400 dialysis cases per month', 'Provides hemodialysis and CRRT in the dialysis center', dialysis centre running since 2004. 18 domains, official site, coords within 1.1 km of PIN. A clear, sensible pick.
2. **NAV Imperial Hospital and Research Centre** (Jaipur, Rajasthan; private hospital), 2.2 km, grade 3, high confidence, evidence moderate.
   Quoted: "Dialysis machines using cutting-edge technology for renal failure treatment".
   Mark: **right**. NAV Imperial, Jaipur, 2.2 km. Equipment fact 'Dialysis machines ... for renal failure treatment' plus 'Experienced team of nephrologists'. That is only 2 facts but they are direct, with 15 domains and coords that match the PIN. Sensible.
3. **Monilek Hospital and Research Center** (Jaipur, Rajasthan; private hospital), 5.4 km, grade 3, low confidence, evidence thin.
   Quoted: "12 Japanese Toray dialysis machines".
   Mark: **right**. Monilek Hospital, Jaipur, 5.4 km. '12 Japanese Toray dialysis machines' plus several facts that list a Nephrology department. That supports the service. The 'Low' confidence label undersells the record (14 domains, official site), but the pick is correct. Note: SMS Hospital (public, 1.5 km) appears only as unverified because its record never mentions dialysis, which is the right handling.

Unverified nearby shown below the cards: 3.

### `brief_csection_lucknow`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Lucknow, state hint Uttar Pradesh". Method recipe, radius 50 km.
- Expected `ok`, got `ok`. Why expected: Brief query 2. State capital; expect hospitals that name caesarean section within 50 km.
- Location: Centre of Lucknow district, Uttar Pradesh, about 38 km across. Matched the district name 'LUCKNOW' exactly in Uttar Pradesh. This is the district centre, not an exact location.
- Answer: 3 of 14 recommendable facilities within 150 km, best first.

1. **BNK Hospital** (Lucknow, Uttar Pradesh; private hospital), 2.9 km, grade 3, high confidence, evidence strong.
   Quoted: "Performs Lower Segment Caesarean Section (LSCS)"; "On-site Blood Bank"; "Modular Operation Theatre".
   Mark: **right**. Record names LSCS in two procedure facts, an on-site blood bank (two facts), a modular OT, NICU with ventilators and 24/7 emergency. Location ok (7 km from its PIN centre), 2.9 km from the Lucknow district centre. All three CEmOC requirements are clearly met. Good first choice.
2. **Elite Hospital** (Lucknow, Uttar Pradesh; None hospital), 5.1 km, grade 2, medium confidence, evidence moderate.
   Quoted: "Cesarean delivery services".
   Missing: no fact in the record mentions blood bank or transfusion; no fact in the record mentions anaesthesia or operating theatre.
   Mark: **defensible**. C-section is supported ('Cesarean delivery services', 'Cesarean delivery is provided'), but the record has no blood bank, no OT and no anaesthesia fact. Its official_website is a goo.gl maps link. Better options existed: St. Joseph Hospital Lucknow (1980afa5, 7.6 km, corroborated: Caesarean Section, five OTs, blood bank with component separation, 180 beds; only a minor possible_merge flag), and Krishna Medical Centre, which actually scored higher (0.780 vs 0.767) and also names OTs. Elite only goes first because a 0.02 tie-break is settled by 0.1 km of distance.
3. **Krishna Medical Centre** (Lucknow, Uttar Pradesh; private hospital), 5.2 km, grade 2, medium confidence, evidence moderate.
   Quoted: "Obstetrics and gynaecology services including ANC, normal delivery, C-section and high-risk pregnancy care".
   Missing: no fact in the record mentions blood bank or transfusion.
   Mark: **right**. Strong record: C-section named in a capability ('normal delivery, C-section and high-risk pregnancy care') and a procedure fact, 'Four modular major operating theatres', multiple NICU facts, 24/7 emergency, 107 beds, 8 domains. Only a blood bank fact is missing, which the card states correctly. Sensible at 5.2 km. It should rank above Elite.

Unverified nearby shown below the cards: 3.

### `brief_dialysis_rural_bihar`

- Query: Dialysis (`dialysis`), place "Jokihat, state hint Bihar". Method recipe, radius 50 km.
- Expected `ok`, got `no_reliable_option` (not as expected). Why expected: Brief query 3. Rural block in Araria district, one of India's poorest. Purnia, about 45 km away, is the regional hub; expect a weak answer near the radius edge, and abstention would also be honest.
- Location: Jokihat (post office area, PIN 854329), Araria district, Bihar. Matched the post office name 'Jokihat' exactly in Bihar.
- Answer: No reliable option nearby: no facility in this dataset shows reliable evidence of dialysis within 50 km. This does not mean there is none; the data may be missing it.

Beyond-radius pointer shown: Raiganj Government Medical College And Hospital (Raiganj, Uttar Dinajpur), 75.8 km, low confidence.

Mark: **abstention_right**. Resolution is correct (Jokihat PO, PIN 854329, Araria). Only 7 facilities lie within 60 km and none has any fact mentioning dialysis, nephrology or kidney care. The nearest dialysis evidence is in Raiganj, West Bengal, 75.8 to 77.2 km away, so abstaining at 50 km is honest. Purnia (about 45 km) has only dental, skin and IVF clinics in the data. 'GMCH Purnea' is a merged record placed at Bettiah (26.80N, 84.50E) with no dialysis facts. Nit on the beyond-radius pick: it shows Raiganj GMCH, which rests on one fact and is marked low confidence, over Jeevan Rekha Hospital at 77.2 km, which has seven dialysis facts ('Provides dialysis treatment', 'Dialysis unit on-site'). Its state is also shown as 'Uttar Dinajpur' instead of West Bengal.


### `brief_highrisk_maharashtra`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Nashik, state hint Maharashtra". Method recipe, radius 50 km.
- Expected `ok`, got `ok`. Why expected: Brief query 4. A Maharashtra district with a large city; expect C-section evidence within 50 km.
- Location: Centre of Nashik district, Maharashtra, about 137 km across. Matched the district name 'NASHIK' exactly in Maharashtra. This is the district centre, not an exact location.
- Answer: 3 of 27 recommendable facilities within 150 km, best first.

1. **Bagul Nursing Home & Dr. Bagul’s Hope IVF Center** (Nashik, Maharashtra; private hospital), 26.3 km, grade 2, medium confidence, evidence moderate.
   Quoted: "Delivery by cesarean section".
   Missing: no fact in the record mentions blood bank or transfusion.
   Mark: **right**. Bagul Nursing Home, Nashik, 26.3 km from the district centre (about 6 km from Nashik city). Strong obstetric record: 'Delivery by cesarean section' repeated across several facts, two OB/GYN specialists, 'Labour room with foetal monitors', 'Operation theatre equipped with anesthesia setup'. No blood bank fact, and the card says so. Best-evidenced EmOC option in Nashik. Minor: the location note says 'consistent with its PIN code' although coord_pin_km is 20.2.
2. **Hrudayasparsh Multispeciality Hospital** (Nashik, Maharashtra; private hospital), 18.2 km, grade 2, medium confidence, evidence moderate.
   Quoted: "Obstetric services including normal and cesarean deliveries".
   Missing: no fact in the record mentions blood bank or transfusion; no fact in the record mentions anaesthesia or operating theatre.
   Mark: **defensible**. Hrudayasparsh, Nashik, 18.2 km. Supported by 'Obstetric services including normal and cesarean deliveries' and 'Cesarean delivery'. The record is thin, though: 17 facts, 2 domains, source trust 0.53, and no OT, anaesthesia or blood bank fact. Om Hospital (12.7 km, 143 facts, 'Caesarean Section (C Section)', VBAC, laparoscopic Obs & Gyn) and Prapti Women's Hospital (an OB/GYN specialty hospital, 71 facts) are within radius and better documented.
3. **Currae Hospital** (Thane West, Maharashtra; private hospital), 135.0 km, grade 3, high confidence, evidence strong.
   Quoted: "Provides a comprehensive range of women's health services including normal delivery, C-section, high-risk pregnancy management, and painless delivery"; "Blood storage facility"; "Facilities include labour room, birthing suite, LDRP, operation theatres, IVF lab, Andrology lab, ICU, NICU, Emergency Room, X-Ray, Mammography, 2D & 4D Sonography, 24x7 Pathology, and 24x7 Pharmacy".
   Mark: **defensible**. Currae Hospital, Thane, 135 km. The record fully supports it: C-section, 'Blood storage facility', operation theatres, NICU. But for emergency obstetrics with a 50 km radius, a hospital 135 km away fills a slot that three grade-2 C-section facilities inside the radius could have taken: Om Hospital 12.7 km, Sukhatme 14.4 km ('Performs caesarean sections'), Prapti 20.7 km. The formula's 0.5 evidence weight lets corroboration outweigh a 7x distance gap. The card does flag 'Beyond the usual 50 km'.

Unverified nearby shown below the cards: 3.

### `brief_trauma_highway`

- Query: Trauma and accident care (`trauma`), place "Kotputli, state hint Rajasthan". Method recipe, radius 50 km.
- Expected `ok`, got `ok`. Why expected: Brief query 5. Town on NH48 between Delhi and Jaipur. Trauma has no v0.1 recipe, so until v0.2 grades load this goes to the keyword fallback.
- Location: Kotputli (post office area, PIN 303108), Jaipur district, Rajasthan. Matched the post office name 'Kotputli' exactly in Rajasthan.
- Answer: 3 of 50 recommendable facilities within 150 km, best first.

1. **Shri Shyam Hospital** (Kotputli, Rajasthan; private hospital), 0.6 km, grade 3, high confidence, evidence strong.
   Quoted: "Head of Trauma Unit identified among listed doctors"; "State-of-the-art operating theatre"; "Provides ICU care with state-of-the-art equipment and 24/7 monitoring".
   Mark: **right**. In Kotputli itself (0.6 km, PIN consistent). Record: 'Specializes in orthopedics and trauma care', 'Orthopedic and trauma surgery services', two OT facts, ICU, 24x7 emergency and ambulance. The quoted trauma evidence ('Head of Trauma Unit identified among listed doctors') is the weakest fact available, and there is no blood bank fact, but the ICU satisfies the requirement. A correct highway trauma pick.
2. **Rainbow Multispeciality Hospital And Trauma Centre, Moti Nagar, Narnaul** (Narnaul, Haryana; private hospital), 40.1 km, grade 3, high confidence, evidence strong.
   Quoted: "Has 24x7 emergency and trauma centre"; "Modular operation theatres"; "Specialities include Anesthesiology, General Surgery, Open Surgery, Laparoscopic Surgery, Joint Replacement, Maxillofacial Surgery, Neonatology, Orthopaedics, Obstetrics & Gynecology, Urology, General Medicine, Intensive Care, Endocrinology, ENT, Dental Surgery, Oncosurgery, Mastoidectomy, Tympanoplasty".
   Mark: **right**. Narnaul, Haryana, 40 km away (location ok). Record: 'Has 24x7 emergency and trauma centre', 'Trauma centre', modular OTs, ICU/PICU/NICU equipment facts. The ICU quote on the card is a long specialty list ('Intensive Care'), a weak choice when a direct 'Intensive care unit (ICU)' equipment fact exists. The service is supported.
3. **Sachkhand Hospital** (Nimrana, Rajasthan; None hospital), 35.7 km, grade 3, high confidence, evidence strong.
   Quoted: "Provides ambulatory emergency and trauma care"; "Operating theatres equipped with anesthesia machines and patient monitoring systems"; "Has Intensive Care Unit".
   Mark: **right**. Nimrana, 35.7 km (location ok). Record: 'Trauma care center', 'Emergency and trauma services available', OTs with anaesthesia machines, ICU, 'Blood bank on-site', 24/7. It is the most complete trauma record of the three but ranks below the farther Rainbow on source trust (3 domains). Still a sensible pick.

Unverified nearby shown below the cards: 3.

### `line_nicu_pune`

- Query: Newborn intensive care (NICU) (`nicu`), place "Pune, state hint Maharashtra". Method recipe, radius 75 km.
- Expected `ok`, got `ok`. Why expected: One per service line: newborn intensive care in a large city.
- Location: Centre of Pune district, Maharashtra, about 133 km across. Matched the district name 'PUNE' exactly in Maharashtra. This is the district centre, not an exact location.
- Answer: 3 of 50 recommendable facilities within 225 km, best first.

1. **Sahyadri Hospitals** (Pune, Maharashtra; private hospital), 8.2 km, grade 3, high confidence, evidence strong.
   Quoted: "NICU on-site"; "Critical Care Unit equipped with multiparameter monitors and ventilators".
   Mark: **right**. Sahyadri Hospitals: 'NICU on-site' and 'Neonatal Intensive Care Unit (NICU) facilities', plus a CCU with ventilators. It has 25 domains and an official site, and the location matches its PIN (411004). Caveat: this is a chain-level record (it mentions the Momstory branch and Shivaji Nagar, Deccan and Baner), so the NICU may be at a different Sahyadri branch than the mapped point 8.2 km out. Every branch is in Pune, so the choice is still sensible.
2. **Dhanwantari Hospital** (Pune, Maharashtra; private hospital), 1.1 km, grade 3, medium confidence, evidence strong.
   Quoted: "NICU and PICU units"; "PICU with 2 ventilators".
   Flag: `possible_merge` (minor): The record's own text places it in Thane, Maharashtra, but its PIN code and city put it in Jalgaon, Maharashtra: it may mix facts from a facility of the same or a similar name elsewhere.
   Mark: **wrong**. Dhanwantari Hospital is a badly merged record of 10+ different 'Dhanvantari' hospitals: Chalisgaon (its PIN 424101, 246 km from its map point), Saravali/Thane, Neral/Karjat, Matunga, Navi Mumbai, Panvel, Ahmednagar, Paithan, Nashik and Nigdi. The NICU facts ('NICU with 3 phototherapy machines', '12 cots', 'PICU with 2 ventilators') cannot be tied to the Pune point 1.1 km from the centre. The ventilator is a PICU ventilator, not a NICU one. The possible_merge flag is graded only 'minor', which is too lenient. The record does not establish a NICU at this location.
3. **Dr DY Patil Medical College and Hospital, Pune, India** (Pune, Maharashtra; private hospital), 12.7 km, grade 3, medium confidence, evidence strong.
   Quoted: "Operates 24x7 donor milk bank and Comprehensive Lactation Management, Training and Research Center (YHMB & CLMTRC) within the hospital near NICU and OBG wing"; "Mechanical ventilation is provided to patients in the Department of Critical Care Medicine".
   Flag: `location_conflict` (minor): The record's scraped map point was 406 km from its PIN code's area; it is shown at the PIN code's centre, so its position is approximate.
   Mark: **defensible**. DY Patil Medical College, Pimpri. It was correctly moved to its PIN centre (411018) after the scraped point was 406 km off. The NICU evidence is indirect: a milk bank 'near NICU and OBG wing'. The ventilator evidence is adult ('Mechanical ventilation ... Department of Critical Care Medicine'), not newborn ventilation. A real tertiary NICU almost certainly exists, but calling the evidence 'strong' at grade 3 overstates the record.

Unverified nearby shown below the cards: 3.

### `line_icu_patna`

- Query: Intensive care with ventilator (`icu`), place "Patna, state hint Bihar". Method recipe, radius 75 km.
- Expected `ok`, got `ok`. Why expected: One per service line: intensive care with ventilator in Bihar's capital.
- Location: Centre of Patna district, Bihar, about 105 km across. Matched the district name 'PATNA' exactly in Bihar. This is the district centre, not an exact location.
- Answer: 3 of 50 recommendable facilities within 225 km, best first.

1. **Shrinivas Hospital** (Patna, Bihar; private hospital), 1.5 km, grade 3, high confidence, evidence strong.
   Quoted: "9 ICU beds"; "Neonatal Intensive Care Unit (NICU) with servo-controlled warmer, LED phototherapy, syringe pumps, volumetric pumps, ventilator, CPAP and exchange transfusion".
   Mark: **right**. Shrinivas Hospital, Patna, 1.5 km. '9 ICU beds', and 'ICU is equipped with multipara monitors, syringe pumps, infusion pumps, defibrillators'. A 50-bed general/multispecialty hospital. Caveat: the only ventilator evidence is in the NICU ('NICU ... ventilator, CPAP'), so the card's ventilator quote is a pediatric unit, not the adult ICU. ICU support itself is clear.
2. **Ford Hospital And Research Center** (Patna, Bihar; private hospital), 1.8 km, grade 3, high confidence, evidence strong.
   Quoted: "Intensive Care Unit with ventilators and patient monitoring systems".
   Mark: **right**. Ford Hospital, Patna, 1.8 km. 'Intensive Care Unit with ventilators and patient monitoring systems', 'Advance Critical Care hospital bed', ICU, NICU and PICU, 24/7 emergency. 17 domains, official site. A clear general ICU.
3. **Patna Children Hospital And Newborn Care Centre** (Patna, Bihar; private hospital), 2.0 km, grade 3, high confidence, evidence strong.
   Quoted: "Paediatric Critical Care"; "Neonatal CPAP and Ventilator support".
   Mark: **defensible**. Patna Children Hospital and Newborn Care Centre, 2.0 km. The record strongly supports pediatric critical care only: NICU, PICU, 'Neonatal CPAP and Ventilator support', Drager Babylog. For a generic ICU query, an adult patient would likely be turned away. General hospitals with grade-3 ICU evidence were equally close and should have taken the slot: Nectar Hospital (0.3 km from the centre), Samarpan Trauma and Research Centre, Savera Cancer Hospital. The card does not say the hospital is pediatric-only.

Unverified nearby shown below the cards: 3.

### `line_cardiac_bhubaneswar`

- Query: Heart surgery (bypass, valve) (`cardiac_surgery`), place "Bhubaneswar, state hint Odisha". Method recipe, radius 250 km.
- Expected `ok`, got `ok`. Why expected: One per service line: heart surgery in a state capital with medical colleges.
- Location: Bhubaneswar (post office area, PIN 751001), Khordha district, Odisha. Matched the post office name 'Bhubaneswar' exactly in Odisha.
- Answer: 3 of 50 recommendable facilities within 750 km, best first.

1. **Kalinga Hospital** (Bhubaneswar, Odisha; private hospital), 6.5 km, grade 3, medium confidence, evidence strong.
   Quoted: "Has cardiology and cardiac surgery department"; "Center of Excellence includes departments such as Cardiology, Neurology, Neurosurgery, Orthopaedics & Joint Replacement, Spine Surgery, Ophthalmology, ENT, Dermatology, Dentistry & Maxillofacial Surgery, Gastroenterology, General Medicine, General Surgery, Intensive Care Unit, Laboratory, Nephrology, Obstetrics & Gynaecology, Paediatrics & Neonatology, Pediatric Surgery, Plastic & Reconstructive Surgery, Psychiatry, Pulmonology, Radiology & Interventional Radiology, Urology & Renal Transplant, Cardiothoracic Vascular Surgery, Emergency & Trauma, Endocrinology & Diabetology, Gastrointestinal Surgery, Nutrition & Dietetics, Smile Train, Blood Bank & Transfusion Medicine, Anesthesia, Preventive Health Care.".
   Flag: `location_conflict` (minor): The record's scraped map point was 84 km from its PIN code's area; it is shown at the PIN code's centre, so its position is approximate.
   Mark: **defensible**. Heart surgery rests on a department-level statement only ('Has cardiology and cardiac surgery department'). ICU and blood bank come from one long department list. No CABG, valve or open-heart procedure is named, which the recipe text asks for. Location was moved to the PIN centre after an 84 km conflict, but PIN 751016 is in Bhubaneswar, so that is acceptable. Ranked #1 above AIIMS, whose record is far more specific. Grade 3 here comes from list matching, not substantive evidence.
2. **All India Institute of Medical Sciences** (Bhubaneshwar, Odisha; public hospital), 7.6 km, grade 2, medium confidence, evidence moderate.
   Quoted: "Beating-heart coronary artery bypass grafting (CABG) surgeries".
   Missing: no fact in the record mentions blood bank or transfusion.
   Mark: **right**. Best-evidenced cardiac surgery record in the area: beating-heart CABG, valve repair and replacement, MICS, paediatric ASD/VSD/PDA repairs, TAVR, a heart-lung machine with IABP, and twin modular OTs with a 13-bed cardiothoracic ICU. 900 beds, 7.6 km, location ok. It is grade 2 only because no blood bank fact exists. It should have been #1.
3. **Jaiprakash Hospital and Research Centre, Rourkela, Sundergarh** (Rourkela, Odisha; private hospital), 242.2 km, grade 3, high confidence, evidence strong.
   Quoted: "Cardiology & Cardiothoracic Surgery"; "ICU available"; "Operates a 24/7 blood bank".
   Mark: **defensible**. The record supports cardiothoracic surgery capacity: 'Cardiology & Cardiothoracic Surgery', a Cardiac OT suite, cardiac ICU and a 24/7 blood bank. No named procedure. It is in Rourkela, 242 km from Bhubaneswar, while closer supported options exist: IGKC Multispecialty Hospital (268245b0, 7.3 km, procedure 'Minimal access cardiac bypass surgery'), Utkal Hospital (CTVS, 6.6 km) and Ashwini Hospital Cuttack (22 km). Sending a Bhubaneswar patient 242 km is not the sensible third option.

Unverified nearby shown below the cards: 3.

### `line_cathlab_indore`

- Query: Heart attack care (angiography, angioplasty) (`cath_lab`), place "Indore, state hint Madhya Pradesh". Method recipe, radius 100 km.
- Expected `ok`, got `ok`. Why expected: One per service line: heart attack care (angioplasty) in a large city.
- Location: Centre of Indore district, Madhya Pradesh, about 63 km across. Matched the district name 'INDORE' exactly in Madhya Pradesh. This is the district centre, not an exact location.
- Answer: 3 of 20 recommendable facilities within 300 km, best first.

1. **Shri Aurobindo Institute of Medical Sciences** (Indore, Madhya Pradesh; private hospital), 9.2 km, grade 3, high confidence, evidence strong.
   Quoted: "Angioplasty with balloon catheter in Cath Lab"; "ICU & HDU care"; "24/7 emergency care".
   Mark: **right**. SAIMS Indore: 'Angioplasty with balloon catheter in Cath Lab', 'Cath Lab System', 'ICU & HDU care' and '24/7 emergency care', from 26 domains. The location matches PIN 453555 (Sanwer Road), 9.2 km out. A clear, sensible choice.
2. **V One Hospital** (Indore, Madhya Pradesh; private hospital), 2.7 km, grade 3, high confidence, evidence strong.
   Quoted: "Offers interventional cardiology procedures including coronary angiography and percutaneous transluminal coronary angioplasty (PTCA)"; "ICU Available"; "Trauma and Emergency Care available".
   Mark: **right**. V One Hospital, 2.7 km away: 'coronary angiography and percutaneous transluminal coronary angioplasty (PTCA)' and other interventional procedures, plus 'ICU Available' and '24/7 emergency care'. Location consistent with PIN 452001. Arguably it should rank first, since it is closer and equally corroborated.
3. **Hamidia Hospital** (Bhopal, Madhya Pradesh; private hospital), 168.3 km, grade 3, high confidence, evidence strong.
   Quoted: "Angiography performed in the Cardiology catheterization laboratory (Cath Lab)"; "Intensive Care Units with dedicated beds"; "Emergency duty cum admission days for Orthopedics are defined per unit".
   Mark: **defensible**. Hamidia Hospital, Bhopal: the cath lab evidence is real ('Angiography performed in the Cardiology catheterization laboratory'). But at 168 km it is well beyond the 100 km radius, which is a poor third card for a heart-attack query in Indore. Medanta Indore (5.4 km, 'Artis-Zeego Endovascular Surgical Cath Lab') and Indira Memorial (2.7 km, 'Cath Lab') are inside Indore, at grade 2 with thin evidence. The 24-hour evidence is weak ('Emergency duty cum admission days for Orthopedics'). The operator is labelled private, but the record says 'government-run facility' (Gandhi Medical College), and its description is contaminated ('Private alcohol detoxification centre').

Unverified nearby shown below the cards: 3.

### `line_mri_coimbatore`

- Query: MRI scan (`mri`), place "Coimbatore, state hint Tamil Nadu". Method recipe, radius 100 km.
- Expected `ok`, got `ok`. Why expected: One per service line: MRI in a large city.
- Location: Centre of Coimbatore district, Tamil Nadu, about 60 km across. Matched the district name 'COIMBATORE' exactly in Tamil Nadu. This is the district centre, not an exact location.
- Answer: 3 of 49 recommendable facilities within 300 km, best first.

1. **G. Kuppusamy Naidu Memorial Hospital** (Coimbatore, Tamil Nadu; private hospital), 1.4 km, grade 3, high confidence, evidence moderate.
   Quoted: "Cardiac CT imaging facility and Cardiac MRI capability"; "AI-based Bio-Matrix 3 Tesla MRI System (Magnetom Vida).".
   Mark: **right**. G. Kuppusamy Naidu Memorial Hospital, Coimbatore, 1.4 km. 'AI-based Bio-Matrix 3 Tesla MRI System (Magnetom Vida)' and 'Cardiac MRI capability'. A specific named scanner, coords match the PIN. Clear.
2. **Ganga Hospital, Coimbatore** (Coimbatore, Tamil Nadu; private hospital), 2.8 km, grade 3, high confidence, evidence strong.
   Quoted: "High-resolution 1.5 Tesla MRI scanner"; "CT scan and MRI scan capabilities for head and neck and chest imaging".
   Mark: **right**. Ganga Hospital, Coimbatore, 2.8 km. 'High-resolution 1.5 Tesla MRI scanner' and 'MAGNETOM Lumina MRI scanner (Siemens Healthineers)'. 25 domains. Clear.
3. **Shree Sivaa Hospital** (Ganapathy, Tamil Nadu; private hospital), 4.6 km, grade 3, high confidence, evidence strong.
   Quoted: "MRI diagnostic capability"; "Magnetic Resonance Imaging (MRI) is used for diagnostic imaging".
   Mark: **right**. Shree Sivaa Hospital, Ganapathy (a Coimbatore suburb), 4.6 km. The service appears in three fields: capability 'MRI diagnostic capability', procedure 'Magnetic Resonance Imaging (MRI) is used for diagnostic imaging', equipment 'MRI scanner'. Generic wording, but consistent. Sensible.

Unverified nearby shown below the cards: 3.

### `line_ct_guwahati`

- Query: CT scan (`ct`), place "Guwahati, state hint Assam". Method recipe, radius 75 km.
- Expected `ok`, got `ok`. Why expected: One per service line: CT scan in the North East's largest city.
- Location: Centre of Kamrup Metro district, Assam, about 24 km across. Matched the district name 'KAMRUP METRO' exactly (old or alternative name) in Assam. This is the district centre, not an exact location.
- Answer: 3 of 30 recommendable facilities within 225 km, best first.

1. **Dr. Bhubaneswar Borooah Cancer Institute (BBCI), Guwahati, Assam** (Guwahati, Assam; public hospital), 0.6 km, grade 3, high confidence, evidence strong.
   Quoted: "Performs contrast-enhanced computed tomography imaging of the neck, chest, and abdomen for cancer staging"; "Performs PET-CT-based diagnostic workups for cancer diagnosis and staging".
   Mark: **defensible**. CT is clearly supported: a contrast-enhanced CT procedure, a CT scanner, a PET-CT scanner and a CT simulator. Location ok, 0.6 km. However, this is a dedicated cancer institute and every CT fact is framed as cancer staging. For a general CT need (often emergency: stroke, trauma), general hospitals 1 km farther with CT and emergency facts (Arya Hospital, Narayana) are the more sensible first choice.
2. **Narayana Superspeciality Hospital, Guwahati, Assam** (Guwahati, Assam; private hospital), 1.5 km, grade 3, high confidence, evidence strong.
   Quoted: "Imaging and diagnostic imaging services including CT and MRI"; "CT coronary angiography".
   Mark: **right**. Record: 'Imaging and diagnostic imaging services including CT and MRI', CT scanner(s) as equipment, CT coronary angiography. Multispecialty hospital, 1.5 km, location ok, 20 domains. A clear and sensible pick.
3. **Arya Hospital** (Guwahati, Assam; public hospital), 1.6 km, grade 3, high confidence, evidence strong.
   Quoted: "On-site CT scan facility"; "CT scan imaging is available for emergency diagnostics".
   Mark: **right**. Record: 'On-site CT scan facility', 'CT scan imaging is available for emergency diagnostics', CT scanner equipment. 1.6 km, location ok. A clear, sensible general-hospital CT option.

Unverified nearby shown below the cards: 3.

### `line_burns_nagpur`

- Query: Burn care (`burns`), place "Nagpur, state hint Maharashtra". Method recipe, radius 200 km.
- Expected `ok`, got `ok`. Why expected: One per service line: burn care with a 200 km radius.
- Location: Centre of Nagpur district, Maharashtra, about 94 km across. Matched the district name 'NAGPUR' exactly in Maharashtra. This is the district centre, not an exact location.
- Answer: 3 of 32 recommendable facilities within 600 km, best first.

1. **Mure Memorial Hospital** (Nagpur, Maharashtra; private hospital), 0.6 km, grade 3, high confidence, evidence strong.
   Quoted: "Departments include Medicine, General Surgery, Obstetrics & Gynecology, Pediatrics, ENT, Ophthalmology, Orthopedics & Trauma Care, Oncology, Dermatology, Psychiatry, Physiotherapy, Dietetics, Dental, Burns unit and Plastic Surgery"; "ICU and ICCU units on site"; "Has four well-equipped operating theatres".
   Mark: **right**. Mure Memorial Hospital, 0.6 km away: 'Burns unit and Plastic Surgery' in its department list, an on-call plastic surgeon, 'ICU and ICCU units on site' and 'four well-equipped operating theatres'. The location matches PIN 440001. The burns evidence comes from a department list, but it names a burns unit specifically.
2. **Maya Hospital & Research Institute** (Nagpur, Maharashtra; private hospital), 4.4 km, grade 3, high confidence, evidence strong.
   Quoted: "Departments include Medicine, Surgery, Diagnostic, and Orthopedics & Burn Injuries"; "24/7 ICU availability."; "Operating theatres (OTs)".
   Mark: **defensible**. Maya Hospital is a 30-bed hospital with 5 ICU beds. Its burns evidence is a combined department 'Orthopaedics & Burn Injuries' and 'burn injury management'. That shows burn treatment, but not a burns unit. Better second choices existed: Vedanta (1.5 km, ranked 3rd), with a burns and plastic surgery department and laminar-flow OT. AIIMS Nagpur (13 km) lists a 'Burns and Plastic Surgery' department, but the model graded it only 1, inconsistent with Mure's grade 3 for similar department-list evidence.
3. **Vedanta Super Speciality Hospital** (Nagpur, Maharashtra; private hospital), 1.5 km, grade 3, high confidence, evidence strong.
   Quoted: "Provides burns treatment"; "Fully equipped ICU with central oxygen"; "Modular operation theatre with laminar air flow".
   Mark: **right**. Vedanta Super Speciality Hospital, 1.5 km away: 'Provides burns treatment', a plastic and reconstructive surgery department that includes burns, 'Fully equipped ICU with central oxygen' and a 'Modular operation theatre with laminar air flow'. Location consistent with PIN 440010. It has only 4 source domains, but the evidence is specific. It should probably rank above Maya.

Unverified nearby shown below the cards: 3.

### `line_neuro_chandigarh`

- Query: Head injury and brain surgery (`neurosurgery`), place "Chandigarh, state hint Chandigarh". Method recipe, radius 150 km.
- Expected `ok`, got `ok`. Why expected: One per service line: brain surgery near a large teaching hospital city.
- Location: Centre of Chandigarh district, Chandigarh, about 13 km across. Matched the district name 'CHANDIGARH' exactly in Chandigarh. This is the district centre, not an exact location.
- Answer: 3 of 50 recommendable facilities within 450 km, best first.

1. **Neelam Hospital** (Rajpura, Punjab; private hospital), 26.7 km, grade 3, high confidence, evidence strong.
   Quoted: "Offers Neurosurgery"; "Has PICU and NICU units"; "CT Scan".
   Mark: **right**. Neelam Hospital, Rajpura, 26.7 km. 'Department of Neurosurgery', 'Provides neurosurgery procedures', CT and MRI on site. The ICU quote is 'Has PICU and NICU units', which is pediatric, but another fact ('CRRT ... for critically ill ICU patients') implies an adult ICU. Reasonable within the 150 km neurosurgery radius. Closer Mohali hospitals (Max 6.4 km, Fortis 6.7 km, IVY 8.8 km, Cheema 6.2 km) are only grade 2 in this data.
2. **Healing Touch Super Speciality Hospital** (Ambala City, Haryana; private hospital), 36.9 km, grade 3, high confidence, evidence strong.
   Quoted: "Neurosurgery"; "12 ICU beds"; "CT Scan".
   Mark: **right**. Healing Touch Super Speciality, Ambala City, 36.9 km. 'Neurosurgery', '12 ICU beds', CT and MRI scanners in several fields. Supported, coords match the PIN. Acceptable for neurosurgery at 37 km. It ranks above Alchemist (10.2 km) only because of Alchemist's 0.10 location penalty.
3. **Alchemist Hospital** (Gurgaon, Haryana; private hospital), 10.2 km, grade 3, medium confidence, evidence strong.
   Quoted: "Performs Neurosurgery"; "Has ICU beds"; "CT angiography capability with 64-slice dual-source CT scanner".
   Flag: `location_conflict` (minor): The record's city reads "Gurgaon" (Gurugram, Haryana), but its PIN code 134112 and map point are in Panchkula, Haryana.
   Mark: **defensible**. Alchemist Hospital, Panchkula, 10.2 km. Neurosurgery, MICU/CCU/CTVS ICU and a 64-slice CT are all named. But the record is visibly merged: city field 'Gurgaon', facts say 'Located in Gurgaon, Sector-53-2' and 'Panchkula (Sector 21)', the official website is ojashospital.co.in, and 'Ojas Hospital, Panchkula' facts are mixed in. The neurosurgery facts may belong to the Gurugram Alchemist or to Ojas. The card shows only a city-mismatch flag, not a possible-merge warning.

Unverified nearby shown below the cards: 3.

### `line_oncology_ahmedabad`

- Query: Cancer treatment (chemotherapy, radiotherapy) (`oncology`), place "Ahmedabad, state hint Gujarat". Method recipe, radius 250 km.
- Expected `ok`, got `ok`. Why expected: One per service line: chemotherapy or radiotherapy in a large city.
- Location: Ahmedabad (post office area, PIN 380001), Ahmadabad district, Gujarat. Matched the post office name 'Ahmedabad' exactly in Gujarat.
- Answer: 3 of 50 recommendable facilities within 750 km, best first.

1. **HCG Hospitals, Ahmedabad** (Ahmedabad, Gujarat; private hospital), 2.0 km, grade 3, high confidence, evidence strong.
   Quoted: "Operates Day Care Chemotherapy Center"; "Uses advanced radiotherapy technologies including CyberKnife, RapidArc, Varian Halcyon, TrueBeam Edge".
   Mark: **right**. Very strong record: Day Care Chemotherapy Center and repeated chemotherapy procedure facts, radiation therapy and brachytherapy, plus CyberKnife, TrueBeam Edge, Halcyon, Tomotherapy and Siemens linacs as equipment. 2.0 km, location ok. Correct first choice.
2. **HCG Aastha Cancer Centre, Ahmedabad** (Ahmedabad, Gujarat; private clinic), 2.0 km, grade 3, high confidence, evidence strong.
   Quoted: "HIPEC treatment available"; "Brachytherapy (Internal Radiation Therapy)".
   Mark: **defensible**. Chemotherapy and radiotherapy are clearly in the record (chemotherapy administration, brachytherapy, CyberKnife and linacs). But it very likely duplicates card 1: identical coordinates to 5 decimals (23.028835, 72.5642), the same equipment list (CyberKnife, TrueBeam Edge, Halcyon, Tomotherapy H, Siemens ONCOR) and the same HCG brand. Its PIN 380060 matches the separate Aastha Oncology Associates record about 7 km away. The duplicate check did not pair it with 4133ceda, so it takes a slot an independent centre (e.g. Pinaksh Cancer Hospital, grade 3, 4.1 km) could have filled. The chemo quote shown is HIPEC, a narrow choice.
3. **Central Cancer Hospital Ahmedabad** (Ahmedabad, Gujarat; private hospital), 1.4 km, grade 3, high confidence, evidence strong.
   Quoted: "Offers surgery, chemotherapy, radiation therapy, immunotherapy, and targeted therapies".
   Mark: **right**. Record: day-care chemotherapy (three facts), 'Offers surgery, chemotherapy, radiation therapy...', and 'Radiation Oncology Center' as equipment. Both arms are named, though radiotherapy relies on one sentence plus the centre name. 1.4 km, location ok. A sensible pick.

Unverified nearby shown below the cards: 3.

### `remote_dialysis_leh`

- Query: Dialysis (`dialysis`), place "Leh, state hint Ladakh". Method recipe, radius 50 km.
- Expected `no_reliable_option`, got `no_reliable_option`. Why expected: Remote: high-altitude town far from the dataset's private hospitals; abstention is the honest answer.
- Location: Leh (post office area, PIN 194101), Leh Ladakh district, Ladakh. Matched the post office name 'Leh' exactly in Ladakh. Note: point from several offices: PIN location for offices with unusable points.
- Answer: No reliable option nearby: no facility in this dataset shows reliable evidence of dialysis within 50 km. This does not mean there is none; the data may be missing it.

Beyond-radius pointer shown: Life Line Care Hospital & Dialysis Centre (Dhalpur, Himachal Pradesh), 247.9 km, high confidence.

Mark: **abstention_right**. The dataset has no facility at all in Leh, Kargil or Ladakh. Within 250 km, the only dialysis evidence is in Himachal at about 248 km (Life Line Care, Dhalpur; Karan Hospital, Palampur). Abstaining at 50 km is honest, and the headline correctly says the data may be missing it. Nit: the beyond-radius card quotes its weakest fact ('Affiliated with NephroPlus dialysis centre') when 'On-site dialysis center' and 'Dialysis treatment services' exist. The resolver's point is a PIN fallback with 89 km precision, but it sits on Leh town.


### `remote_emoc_tawang`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Tawang, state hint Arunachal Pradesh". Method recipe, radius 50 km.
- Expected `no_reliable_option`, got `no_reliable_option`. Why expected: Remote: mountain district on the border; expect no facility in this data with C-section evidence within 50 km.
- Location: Centre of Tawang district, Arunachal Pradesh, about 59 km across. Matched the district name 'TAWANG' exactly in Arunachal Pradesh. This is the district centre, not an exact location.
- Answer: No reliable option nearby: no facility in this dataset shows reliable evidence of emergency c-section and high-risk delivery within 50 km. This does not mean there is none; the data may be missing it.

Beyond-radius pointer shown: Nalbari Maternity Hospital (Nalbari, Assam), 148.3 km, medium confidence.

Mark: **abstention_right**. No facility at all in the dataset lies within 50 km of the Tawang district centre (27.63, 92.10). Within 150 km, 11 facilities exist and only Nalbari Maternity Hospital (Assam, 148 km straight-line, 'LSCS') names C-section. Abstaining, with the honest 'data may be missing it' headline and the 148 km fallback shown, is correct.


### `remote_cardiac_portblair`

- Query: Heart surgery (bypass, valve) (`cardiac_surgery`), place "Port Blair, state hint Andaman and Nicobar Islands". Method recipe, radius 250 km.
- Expected `no_reliable_option`, got `no_reliable_option`. Why expected: Remote: islands over 1,000 km from the mainland; even a 250 km radius covers only sea.
- Location: Port Blair (post office area, PIN 744101), South Andamans district, Andaman and Nicobar Islands. Matched the post office name 'Port Blair' exactly in Andaman and Nicobar Islands.
- Answer: No reliable option nearby: no facility in this dataset shows reliable evidence of heart surgery (bypass, valve) within 250 km. This does not mean there is none; the data may be missing it.

Beyond-radius pointer shown: Hi-Tech Medical College & Hospital, Bhubaneswar (Bhubaneswar, Odisha), 1207.9 km, low confidence.

Mark: **abstention_right**. Only two facilities in the dataset lie within 250 km of Port Blair (an ENT clinic and an eye clinic), both grade 0 with no cardiac facts. No facility within the radius has any heart-surgery evidence, so abstaining is correct. The beyond-radius pointer (Hi-Tech Bhubaneswar, 1,208 km) is thin: one fact, and its PIN 769004 is a Rourkela PIN. It is correctly labelled low.


### `remote_emoc_gadchiroli`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Gadchiroli, state hint Maharashtra". Method recipe, radius 50 km.
- Expected `no_reliable_option`, got `no_reliable_option`. Why expected: Remote: forested tribal district; public facilities dominate and are under-covered in this data.
- Location: Centre of Gadchiroli district, Maharashtra, about 195 km across. Matched the district name 'GADCHIROLI' exactly in Maharashtra. This is the district centre, not an exact location.
- Answer: No reliable option nearby: no facility in this dataset shows reliable evidence of emergency c-section and high-risk delivery within 50 km. This does not mean there is none; the data may be missing it.

Beyond-radius pointer shown: Government Medical College And Hospital Chandrapur (Chandrapur, Maharashtra), 72.6 km, medium confidence.

Mark: **abstention_right**. No facility in the dataset lies within about 70 km of the Gadchiroli district centre. Every candidate is in Chandrapur (72.6 to 76 km) or farther, so nothing qualifies within 50 km, and the result does not change if the point were Gadchiroli town. The beyond-radius card (GMC Chandrapur) is weak. Its quoted C-section fact is a CORDMILK trial protocol line from IGGMC Nagpur, mixed in alongside JJ Mumbai facts. Its OT evidence is a dental OT ('major dental and oral surgeries'). It carries no possible_merge flag. The record does include 'Performs lower segment cesarean sections', which should have been quoted instead.


### `fallback_cataract_hyderabad`

- Query: Keyword search: cataract surgery (`search:cataract surgery`), place "Hyderabad, state hint Telangana". Method keyword, radius 100 km.
- Expected `ok`, got `ok`. Why expected: Fallback: no recipe for cataract surgery; keyword search should find eye hospitals quoting it. Confidence must be low.
- Location: Centre of Hyderabad district, Telangana, about 21 km across. Matched the district name 'HYDERABAD' exactly in Telangana. This is the district centre, not an exact location.
- Answer: 3 of 17 keyword matches within 100 km. Keyword matches are never more than low confidence.

1. **Ramesh Lasik And Laser Centre** (Hyderabad, Telangana; private clinic), 2.3 km, keyword match, low confidence, evidence keyword.
   Quoted: "Cataract Surgery".
   Mark: **defensible**. Ramesh Lasik And Laser Centre, a clinic 2.3 km away. Only one fact: procedure 'Cataract Surgery'. A small 31-fact Lasik clinic. It ranks first only because it is closer, in a tie with much stronger records: Swarup (70,000+ cataract surgeries), Smart Vision (11 cataract-surgery facts), Neo Vision (5). Its coord_pin_km is 43.6, labelled 'ok_same_district', though the raw point does sit in central Hyderabad near PIN 500029.
2. **Swarup Eye Centre** (Hyderabad, Telangana; private clinic), 3.2 km, keyword match, low confidence, evidence keyword.
   Quoted: "Cataract surgery"; "Provides cataract treatment, including cataract surgery"; "FEMTO-Z8 assisted cataract surgery".
   Mark: **right**. Swarup Eye Centre, 3.2 km. 'Has performed over 70,000 cataract surgeries', 'FEMTO-Z8 assisted cataract surgery', a cornea and cataract surgical team. The strongest cataract record nearby. Keyword-match low confidence is appropriate labelling, but the evidence itself is clear.
3. **Smart Vision Eye Hospitals** (Hyderabad, Telangana; private hospital), 5.4 km, keyword match, low confidence, evidence keyword.
   Quoted: "Cataract surgery".
   Mark: **right**. Smart Vision Eye Hospitals, 5.4 km. 'Cataract surgery' and 'Robotic cataract surgery' in procedures, robotic cataract system in equipment, eye hospital with inpatient care. Clear.

### `fallback_antivenom_raipur`

- Query: Keyword search: snake bite anti venom (`search:snake bite anti venom`), place "Raipur, state hint Chhattisgarh". Method keyword, radius 100 km.
- Expected `ok`, got `no_reliable_option` (not as expected). Why expected: Fallback: plan's example of a service with no recipe. Expect few matches; any answer must be low confidence.
- Location: Centre of Raipur district, Chhattisgarh, about 223 km across. Matched the district name 'RAIPUR' exactly in Chhattisgarh. This is the district centre, not an exact location.
- Answer: No facility in this dataset has a fact matching "snake bite anti venom" within 100 km.

Beyond-radius pointer shown: Neurospine Hospital and Revive Critical Care (Nagpur, Maharashtra), 265.1 km, low confidence.

Mark: **abstention_right**. No fact or specialty within 100 km of the Raipur district centre mentions snake bite, venom or ASV. The nearest partial lead is Vardaan Hospital (12.8 km), which has 'Poison Control Center and Burns Treatment Center attached to the ICU unit'. That does not name antivenom, so it is not clear evidence. Abstaining is correct on the record, even though the scenario expected 'ok'. A low-confidence pointer to the Vardaan poison centre would have helped more than the 265 km Nagpur specialty-tag match.


### `ambiguous_aurangabad`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Aurangabad". Method recipe, radius 50 km.
- Expected `needs_location_choice`, got `needs_location_choice`. Why expected: Ambiguous place: districts in Bihar and in Maharashtra. The tool must ask, not guess.
- Location: 2 places match: Centre of Aurangabad district, Maharashtra, about 131 km across; Centre of Aurangabad district, Bihar, about 113 km across.
- Answer: That place name matches more than one place. Pick one to continue.

Mark: **abstention_right**. Correct: 'Aurangabad' with no state is a district in both Maharashtra and Bihar, and both candidates are listed with plausible centres (19.90N 75.34E; 24.70N 84.19E). The tool asks the user to pick instead of guessing, which is the right behaviour.


### `ambiguous_bilaspur`

- Query: Dialysis (`dialysis`), place "Bilaspur". Method recipe, radius 50 km.
- Expected `needs_location_choice`, got `needs_location_choice`. Why expected: Ambiguous place: districts in Chhattisgarh and in Himachal Pradesh. The tool must ask, not guess.
- Location: 2 places match: Centre of Bilaspur district, Chhattisgarh, about 63 km across; Centre of Bilaspur district, Himachal Pradesh, about 37 km across.
- Answer: That place name matches more than one place. Pick one to continue.

Mark: **abstention_right**. 'Bilaspur' matches district names in both Chhattisgarh and Himachal Pradesh, about 1,000 km apart, and no state was given. The answers really differ: Chhattisgarh has Apollo Hospital Bilaspur at 3.4 km (grade 3, strong). Himachal has only thin grade-2 options at 31 km (Gurdev, Ropar) and 42 km (Mandav, Mandi). Asking the user to choose is right.


### `disambiguated_aurangabad_bihar`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Aurangabad, Bihar". Method recipe, radius 50 km.
- Expected `ok`, got `no_reliable_option` (not as expected). Why expected: The same name with the state in the text should resolve to Bihar and answer.
- Location: Centre of Aurangabad district, Bihar, about 113 km across. Matched the district name 'AURANGABAD' exactly in Bihar. This is the district centre, not an exact location.
- Answer: No reliable option nearby: no facility in this dataset shows reliable evidence of emergency c-section and high-risk delivery within 50 km. This does not mean there is none; the data may be missing it.

Beyond-radius pointer shown: Sanjeevan Sparsh Hospital (Chapra, Bihar), 131.9 km, low confidence.

Mark: **abstention_right**. Location resolved correctly to Aurangabad district, Bihar. Only five facilities lie within 60 km: a homoeo clinic, two dental clinics, a diagnostic centre and 'Shree Ram Hospital' Obra, whose facts are company-registry boilerplate plus fever and joint-pain services. None mentions delivery, obstetrics or C-section, so 'no reliable option within 50 km' is correct. The 'ok' expectation does not hold for this data.


### `pin_dialysis_302001`

- Query: Dialysis (`dialysis`), place "302001". Method recipe, radius 50 km.
- Expected `ok`, got `ok`. Why expected: A PIN code as the place (central Jaipur).
- Location: PIN 302001, Jaipur, Rajasthan. Matched PIN 302001 in the India Post directory: the middle of its post offices.
- Answer: 3 of 21 recommendable facilities within 150 km, best first.

1. **Soni Hospital** (Jaipur, Rajasthan; private hospital), 2.6 km, grade 3, high confidence, evidence strong.
   Quoted: "Dialysis center performing over 400 dialysis cases per month".
   Mark: **right**. Soni Hospital, 2.6 km away: 'Dialysis center performing over 400 dialysis cases per month', 'Provides hemodialysis and CRRT', a dialysis centre since 2004, and CRRT equipment. It has 18 domains and an official site, and the location matches PIN 302004. Minor: the record spans several campuses (JLN Marg, Bagru, Bhiwadi), but the dialysis centre is in Jaipur.
2. **NAV Imperial Hospital and Research Centre** (Jaipur, Rajasthan; private hospital), 2.8 km, grade 3, high confidence, evidence moderate.
   Quoted: "Dialysis machines using cutting-edge technology for renal failure treatment".
   Mark: **right**. NAV Imperial Hospital, 2.8 km away: 'Dialysis machines ... for renal failure treatment', a team of nephrologists, and a kidney transplant programme. Location consistent with PIN 302016 (0.3 km). The supporting evidence is moderate, but the combination makes dialysis clearly available.
3. **Monilek Hospital and Research Center** (Jaipur, Rajasthan; private hospital), 4.3 km, grade 3, low confidence, evidence thin.
   Quoted: "12 Japanese Toray dialysis machines".
   Mark: **defensible**. Monilek Hospital, 4.3 km away: '12 Japanese Toray dialysis machines' is specific, and it has a nephrology department and a kidney transplant programme. But grade 3 rests on a single fact, which the tool itself labels thin and low confidence. Surya Hospitals Jaipur (1.5 km, grade 2, 3 supporting facts) and other nearby options with moderate evidence would have been comparable or better third picks.

Unverified nearby shown below the cards: 3.

### `coords_icu_lucknow`

- Query: Intensive care with ventilator (`icu`), place "26.85, 80.95". Method recipe, radius 75 km.
- Expected `ok`, got `ok`. Why expected: Coordinates as the place (Lucknow).
- Location: 26.8500, 80.9500, in Lucknow district, Uttar Pradesh. Read as latitude 26.8500, longitude 80.9500. District from the 5 nearest PIN areas.
- Answer: 3 of 49 recommendable facilities within 225 km, best first.

1. **Dr Ram Manohar Lohia Institute of Medical Sciences** (Lucknow, Uttar Pradesh; public hospital), 5.5 km, grade 3, high confidence, evidence strong.
   Quoted: "PICU has ventilators".
   Mark: **right**. Dr RML Institute of Medical Sciences, a public hospital 5.5 km away. The record supports adult critical care: 'Well-developed Intensive Care Unit', 'ICU/CCU/Respiratory care units are equipped with life-support equipment'. But the card quotes 'PICU has ventilators' for both requirements, so the cited evidence is pediatric when better adult-ICU quotes exist in the same record.
2. **JPS Children Hospital** (Lucknow, Uttar Pradesh; None hospital), 7.1 km, grade 3, high confidence, evidence moderate.
   Quoted: "PICU (Pediatric Intensive Care Unit)"; "Ventilators and CPAP machines for pediatric respiratory support".
   Mark: **defensible**. JPS Children Hospital, 7.1 km. Pediatric-only record: PICU, NICU, 'Ventilators and CPAP machines for pediatric respiratory support'. Its PIN 212601 is 105 km from its coordinates (kept because the city matches), and there are only 6 domains. For a generic ICU query, several general grade-3 ICUs were as close or closer and should have taken the slot: BNK Hospital 3.7 km (strong), Mirus Critical Care Centre 6.2 km (10 facts), St. Joseph 6.8 km, Divine Heart 7.3 km.
3. **Sanjay Gandhi Post Graduate Institute of Medical Sciences** (Lucknow, Uttar Pradesh; public hospital), 12.0 km, grade 3, high confidence, evidence strong.
   Quoted: "Demonstrates advanced pediatric critical care capability at Sanjay Gandhi Post Graduate Institute of Medical Sciences, Lucknow, including the use of HFPPV for oncologic emergencies with pulmonary edema"; "High-frequency ventilator used for HFPPV in the case at SGPGI Lucknow".
   Mark: **defensible**. SGPGIMS, Lucknow, 12.0 km. Certainly a real tertiary ICU, but in this record the only ICU and ventilator evidence is one PubMed case report: HFPPV in a 3-year-old with a desmoplastic small round cell tumour. That is thin, pediatric, case-level evidence, not a statement of an ICU service. It ranks above BNK Hospital (3.7 km, strong ICU evidence) only on source trust. A better choice existed.

Unverified nearby shown below the cards: 3.

### `nonsense_place`

- Query: Emergency C-section and high-risk delivery (`emoc`), place "Xqzvillepur". Method recipe, radius 50 km.
- Expected `location_not_found`, got `location_not_found`. Why expected: A place that does not exist should say so and not guess.
- Location: not found. No district, city or post office matches 'Xqzvillepur'. Try a PIN, a nearby town, or coordinates.
- Answer: No district, city or post office matches 'Xqzvillepur'. Try a PIN, a nearby town, or coordinates.

Mark: **abstention_right**. 'Xqzvillepur' is not a real place. The resolver returned location_not_found with no guessed coordinates and suggested a PIN, a nearby town or coordinates. This is correct behaviour.


## Method

- `scripts/wp5_scenarios.py` ran the 26 scenarios in `data/labels/scenarios.csv`, which were written before any ranking code and are frozen. It records the answer only; it does not judge.
- Independent judges read each answer's quoted facts and the full facility record, looked for better options inside the radius, and marked each card `right`, `defensible` or `wrong`. Answers with no card were marked `abstention_right` or `abstention_wrong`.
- right: the facility's record clearly supports the service and it is a sensible pick at that rank. defensible: the service is supported but a better-evidenced or closer option should have taken the slot, or the evidence is thinner than the label says. wrong: the record does not establish the service at that location.
- Marks are saved per card in `data/labels/scenario_judgments.csv` (58 rows). For 19 cards the judge wrote a shorthand id; those were mapped to the real `facility_id` by scenario and rank and checked against the card name. All matched.
- Latency on the same run: 276 recipe queries, median 0.81 ms, p95 0.88 ms, max 2.22 ms against a 100 ms limit (passed).
