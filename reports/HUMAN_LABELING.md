# Hand labels for the sealed evaluation (about 15 minutes)

The AI judges have already scored the sealed half. Your 40 labels check them. Your labels never change any result; they give the agreement figure the report quotes.

## The file

`data/labels/eval_sealed/human_labels_sealed.csv` (in the main checkout's data folder). 40 rows, shuffled. Each row is one facility record and one service a patient needs. The file does not show which tool picked it, what the judges said, or why it was chosen. Phone numbers and emails are masked.

Do not open `human_key_sealed.csv` until you have finished. It holds the answers.

## What to mark

For each row, read `service_needed`, then `record`. Tip: search the record for the service word (dialysis, caesarean, ICU, ventilator, MRI and so on) first; most rows are decided in 20 seconds.

1. `human_clearly_offers`: `y` or `n`. Does the record itself clearly show that THIS facility offers the service?
   - `y` only if text in the record states or directly shows it.
   - `n` if the record only makes it seem likely (for example only a specialty name such as "nephrology"), if the text that mentions it is about another hospital, branch or city, if it is negated, or if you cannot tell.
   - Judge the text only, not what you know about the place.
2. `human_sensible`: `y` or `n`. Assume the facility does offer the service. Is it a sensible place to send this patient from `place`, given `straight_line_km`?
   - `n` if the record puts the facility somewhere clearly inconsistent with that distance, if it mixes several branches or facilities so it is unclear which building the patient would reach, or if the kind of facility does not fit (a clinic, laboratory, IVF centre for an emergency, and so on).
   - Distance alone is not a reason for `n`.
3. `human_note`: optional, a few words when you were unsure.

Leave the other columns as they are. Save as CSV with the same name.

## Then run

```bash
cd "/Users/hrudaynara/Projects/CMU Datathon"
.venv/bin/python scripts/eval_sealed.py agreement --split sealed
```

It prints and writes `data/labels/eval_sealed/agreement_sealed.json`: raw agreement and Cohen's kappa of the final verdict, judge A and judge B against your labels, for question 1 (40 items) and question 2 (the 30 items the judges answered it for: the tools' picks).

## How to read the number

The 40 items are not a random sample. They are drawn evenly across cells (each tool's picks, facilities read while checking abstentions, judged yes and no, and items where judges A and B disagreed), so hard cases are over-represented. Agreement on them is a check of the judges on hard and easy cases alike, not an estimate of the judges' accuracy on the whole sealed half. If the labels come late, kappa is a footnote (protocol, "Human anchor").
