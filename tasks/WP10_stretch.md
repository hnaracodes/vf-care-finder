# WP10: Stretch

Re-scoped Nov 21 (D023). Start only after WP8 answers the brief's five scenarios on Databricks with real data. Each item is independent. Pick by what most improves a real user's answer, and log the choice. The pre-pivot items (NFHS C-section rates, dentists as screening, who pays) are dropped with the NFHS work.

## A. Plain-language query

One box: "my sister is pregnant and bleeding, we are in Gaya". A model call (Databricks Foundation Model API if Free Edition allows it, else none) turns it into service and location, shows its reading ("Looks like: emergency C-section, near Gaya, Bihar. Change?"), and the normal pipeline answers. The model never writes card text (D030). Test on 20 phrasings, including Hindi in Latin script.

## B. LaQshya cross-check for `emoc`

WP2b already has 1,261 LaQshya-certified public maternity units, 724 of them located on OSM points. For `emoc` scenarios, show how many certified units lie within the radius, and on an abstention, say "N government-certified maternity units are within 50 km but are not in this dataset" with the list's date and source. Outside data is allowed (D015); keep it visibly separate from the dataset's evidence.

## C. Claim plausibility model

Predict whether a facility has a given evidence tag from features that do not contain it (type, operator, beds, doctors, other tags, source domains). A low predicted probability strengthens WP11's `implausible_claim`. Report AUC by line; ship only if it beats the type rule.

## D. Genie space

A Genie space over the WP12 Delta tables so a planner can ask "which districts in Bihar have no corroborated dialysis?" Free, and shows Databricks use. Document its limits: it answers from grades and flags, not from the ranking.

## E. Vector search fallback

Replace TF-IDF in the `search` fallback with Databricks Vector Search (one endpoint on Free Edition). Ship only if it finds clearly better matches on the fallback scenarios, read by hand.

## F. Repair the 30 truncated facility rows

As before. Only if clean.

## G. Data handoff

Grades, evidence and flags as one table with a data card, for Virtue Foundation. Do not publish the raw data publicly without checking the listing's terms.
