# Brief

## The event

- CMU Datathon, Fall 2026, run by the CMU Data Science Club.
- Dates: Nov 21 to 22, 2026 per the club's banner (its competition listing says Nov 20 to 22).
- Theme on the banner: "Turn messy healthcare data into decisions that save lives."
- Sponsors listed: Databricks and United Nations.
- Dataset: "Virtue Foundation Dataset (DAIS 2026)", shared through Databricks. See `01_dataset_listing.md`.
- The January 2026 edition paid $1,000, $600, and $400 to the top three teams.

Source for the above: https://cmudsc.netlify.app/competitions (read Nov 21, 2026).

## The deliverable

- A submitted video. It is not a live demo.
- Due Sunday, Nov 22, 2026. The exact time is not confirmed.
- Hruday wants the video built around an interactive, simulation-style map. See `09_presentation.md`.

## Who

- Hruday Nara (CMU CS, GitHub `hnaracodes`). Whether he has teammates is not confirmed.
- The work is done by Claude Code agents running unattended overnight on Nov 21, with Hruday checking in.

## Open questions for Hruday

Agents: do not block on these. Use the stated default, and log it in `08_decisions_log.md`.

| Question | Default until answered |
| --- | --- |
| Exact submission deadline and time zone | Treat Sunday 12:00 noon ET as the cutoff, with the video recorded by 09:00 ET |
| Video length limit | Plan for 3 minutes, with a 90-second cut ready |
| Judging rubric | Assume insight, rigor, and communication count about equally |
| What else is submitted besides the video (repo, slides, link) | Prepare a public repo link and a hosted link to the map |
| Are external datasets allowed | **Answered Nov 21 (D015): yes, organizers encourage outside data and scraping.** Flag each one used |
| Are paid LLM APIs allowed, and what is the budget | Assume yes, with a hard cap of 30 USD total. Track spend |
| Teammates | Assume solo |

## What the organizers signal they want

These come from the dataset notes, so treat them as requirements:

- "Document how you handle uncertain matches and do not present inferred geography as exact unless verified."
- Check join cardinality before joining on PIN code.
- Treat suppressed NFHS values as missing, not zero, and use low-sample estimates with caution.
- Prefer a spatial join on coordinates over string-matching district names.

## Who built the data, and why it matters for the pitch

Virtue Foundation is a nonprofit that sends medical volunteers to low-income countries. Its VF Match platform depends on knowing which facilities exist and what they can do. Databricks built the pipeline that scrapes, extracts, and consolidates the facility records.

That makes two audiences for the work:

- Virtue Foundation cares about where to send care and whether the facility data can be trusted.
- Databricks cares about data quality, pipelines, and AI that improves the dataset.

A project that hands back a more trustworthy version of their own table speaks to both.

Background reading: https://www.databricks.com/blog/elevating-global-health-databricks-and-virtue-foundation
