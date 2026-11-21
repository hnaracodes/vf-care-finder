# Kickoff prompts

Copy-paste prompts for launching Claude Code agents in this folder. Each agent loads `CLAUDE.md` automatically.

Before launching: fill in `.env` (copy from `.env.example`), and answer what you can in `docs/00_brief.md` under "Open questions".

## Option A: one lead agent that runs everything

```
Read CLAUDE.md, then docs/00_brief.md, docs/04_thesis_and_plan.md, docs/03_data_profile.md,
docs/07_data_contracts.md, and tasks/README.md.

You are the lead for this project. Do WP0 yourself. Then run the work packages in the order
of the dependency graph in tasks/README.md, using subagents in separate git worktrees for
work that can run in parallel (WP1, WP2, and WP8 at the start).

I will be away overnight. Do not stop to ask questions: take the documented defaults, log
decisions in docs/08_decisions_log.md, keep the status board current, and run each work
package's acceptance checks before marking it done. Finish emergency obstetrics end to end
before any polish or stretch work. Do not push, publish, or submit anything.

When you finish or run out of useful work, write a handoff note at reports/HANDOFF.md:
what is done, what is not, the headline numbers with their source files, anything I need
to check, and exactly what I need to do to record and submit the video.
```

## Option B: separate agents per track

Launch WP0 first and wait for it. Then start these in separate terminals, each in its own worktree.

Bootstrap:

```
Read CLAUDE.md and tasks/WP0_bootstrap.md. Do WP0 exactly as written and report the
acceptance checks. Create worktrees for wp1-silver, wp2-geo, and wp8-app.
```

Data track:

```
Read CLAUDE.md, docs/02_data_dictionary.md, docs/03_data_profile.md, docs/07_data_contracts.md.
Do tasks/WP1_silver_facilities.md, then tasks/WP3_teacher_labels.md, then
tasks/WP4_fact_tagger.md. Write the regex baseline grades as early as possible so the
access work is not blocked. Work unattended, log decisions, run the acceptance checks.
```

Geography and modeling track:

```
Read CLAUDE.md, docs/02_data_dictionary.md, docs/03_data_profile.md, docs/07_data_contracts.md.
Do tasks/WP2_geography_nfhs.md, then tasks/WP5_access_scores.md as soon as baseline grades
exist, then tasks/WP6_outcome_model.md and tasks/WP7_simulator.md. Rerun WP5 when final
grades arrive. Work unattended, log decisions, run the acceptance checks.
```

App track:

```
Read CLAUDE.md, docs/09_presentation.md, and the "App data" section of
docs/07_data_contracts.md. Do tasks/WP8_demo_app.md. Start now on mock data with real
post office coordinates, and switch to real data when it appears in app/public/data.
Then do tasks/WP9_story_video.md once the other tracks are done. Work unattended,
log decisions, run the acceptance checks. Do not submit anything.
```

## Morning check for Hruday

1. Open `reports/HANDOFF.md` and `tasks/README.md` (status board).
2. Read `docs/08_decisions_log.md` from D011 onward: these are the calls made without you.
3. Skim `silver/district_crosswalk_review.csv` and the gold set labels; both are places an agent can be confidently wrong.
4. Open the map, run story mode, and check three numbers against `reports/findings.md`.
5. Record the voiceover, export, submit.
