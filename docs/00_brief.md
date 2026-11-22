# Brief

## The event

- CMU Datathon, Fall 2026, run by the CMU Data Science Club.
- Dates: Nov 21 to 22, 2026 per the club's banner (its competition listing says Nov 20 to 22).
- Theme on the banner: "Turn messy healthcare data into decisions that save lives."
- Sponsors listed: Databricks and United Nations.
- Dataset: "Virtue Foundation Dataset (DAIS 2026)", shared through Databricks. See `01_dataset_listing.md`.
- The January 2026 edition paid $1,000, $600, and $400 to the top three teams.

Source for the above: https://cmudsc.netlify.app/competitions (read Nov 21, 2026).

## The official challenge (read Nov 21, re-scope D023)

Source: the organizers' challenge document, "CMU Datathon - Fall 2026 - Challenge" (published Google Doc, link from Hruday). Summary, with short quotes:

- Title: "Your challenge this weekend: find the right care".
- The user: someone who needs a specific kind of care from a place. The doc's examples: a community health worker in rural Bihar who needs dialysis for a patient; an NGO coordinator in Maharashtra finding a hospital for a high-risk delivery; a district planner finding trauma destinations near a highway accident hotspot. In the dataset section: "Where can a patient get dialysis near Jaipur?" and "Which hospital near Lucknow can handle an emergency C-section?"
- The mission: take two inputs, a service (examples: dialysis, emergency C-section, NICU, cardiac surgery, MRI, burn care) and a location (city, district, PIN code, or coordinates), and return the best facility option or options with an evidence-based explanation. Any format: app, dashboard, Genie space, notebook, agent, or a mix. Think about the audience that would really use it.
- The one rule: show reasoning. For every recommendation answer WHY (what evidence says this facility exists, offers the service, and is the best choice for the location) and HOW (what logic, scoring, model or rules turned about 10,000 records into the answer, and what was traded off). A build that cannot explain itself does not meet the requirements, "however good it looks."
- The submission must:
  - "Run on Databricks Free Edition."
  - "Use the provided facility dataset."
  - "Support a clear non-technical user workflow."
  - "Cite the underlying facility text for any important claim, recommendation, score, or ranking."
  - "Communicate uncertainty instead of presenting weak evidence as fact."
- On the data: treat every field as a claim to verify. Some records merge several facilities; some have locations and capabilities that contradict each other; some services have little support. "A confident wrong answer can cost a patient hours." Field coverage per the doc: description 100%, capability 99.7%, procedure 92.5%, equipment 77.0%, year established 47.8%, number of doctors 36.4%, capacity 25.2%.
- Not stated in the doc: judging criteria or weights, submission format, length, or deadline time.

## Databricks Free Edition constraints (D034)

From the Databricks docs, Nov 21; recheck before deploying. Serverless compute only. About 3 apps per account. An app stops 24 hours after it was last started or redeployed, so restart it before judging. One Vector Search endpoint. Non-commercial use only.

## The deliverable

- The tool itself, as a Databricks App on Free Edition (D023, D033).
- Hruday's earlier plan was a submitted video due Sunday, Nov 22 (D005). The official doc does not mention a video. Default: keep a video of the tool (about 3 minutes, plus a 90-second cut), the app, and the repo.

## Who

- Hruday Nara (CMU CS, GitHub `hnaracodes`). Whether Hruday has teammates is not confirmed.
- The work is done by Claude Code agents, partly unattended, with Hruday checking in.

## Open questions for Hruday

Agents: do not block on these. Use the stated default, and log it in `08_decisions_log.md`.

| Question | Default until answered |
| --- | --- |
| Exact submission deadline and time zone | Treat Sunday 12:00 noon ET as the cutoff, with everything ready by 09:00 ET |
| What is submitted (video, app link, repo) and length limits | The official doc does not say. Prepare the app URL, the repo, and a 3-minute video with a 90-second cut |
| Judging rubric | Not in the official doc. Assume the five "must" requirements are pass or fail, and the WHY and HOW explanation decides the rest |
| Are external datasets allowed | **Answered Nov 21 (D015): yes, organizers encourage outside data and scraping.** Flag each one used |
| Are paid LLM APIs allowed, and what is the budget | Assume yes for offline labeling only, with a hard cap of 30 USD total. `ANTHROPIC_API_KEY` is set and valid (D037) |
| Teammates | Assume solo |
| Databricks workspace and Marketplace catalog name | Needed for WP12. Hruday sets up Free Edition, adds the listing, and runs `databricks auth login` |

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
