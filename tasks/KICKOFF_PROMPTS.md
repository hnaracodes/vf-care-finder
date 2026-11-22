# Kickoff prompts

Copy-paste prompts for launching Claude Code agents in this folder after the re-scope (D023). Each agent loads `CLAUDE.md` automatically. The pre-pivot prompts are in git history (`7609dbb`).

Before launching: Hruday sets up Databricks Free Edition with the Marketplace listing (needed by WP12 only), and puts an API key in `.env` if teacher labels are wanted (WP3 Part 3, WP4).

## Lead agent

```
Read CLAUDE.md, then docs/00_brief.md, docs/04_thesis_and_plan.md, docs/07_data_contracts.md,
and tasks/README.md.

You are the lead. Run the work packages in the order of the dependency graph in tasks/README.md,
using subagents for work that can run in parallel: WP2 Part B, WP3 v0.2, WP11, WP8, and the WP12
skeleton can all start now on disjoint files. You alone edit the status board and the decisions
log, and you make one commit per work package on the current branch.

Do not stop to ask questions: take the documented defaults, log decisions, keep the status board
current, and run each work package's acceptance checks before marking it done. emoc and dialysis
must work end to end, deployed, before polish, the trip view, or stretch work. Do not push,
publish, share the app outside the workspace, or submit anything.
```

## One work package

```
Read CLAUDE.md, then tasks/README.md and tasks/WP<N>_<name>.md, and every doc it lists as input.
Do this work package only, writing only the files it owns. Run its acceptance checks and paste
the results in your final message. Log any default you take in docs/08_decisions_log.md.
```
