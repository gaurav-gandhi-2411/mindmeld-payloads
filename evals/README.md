# Warmer eval artifacts

Published eval reports for the Warmer Hinglish embedding work. These back
numbers shown publicly on <https://gaurav-gandhi.vercel.app/work/warmer>.

## Why they live here

They were produced in the private `mindmeld` repo. A public claim backed by a
private artifact is unverifiable by anyone except its author, which is the
weaker arrangement — so the artifacts moved to where the claim is.

This repo was already the public half of that split: the payload data has
always been served from here, and the model and its benchmark are already
public on Hugging Face (`gauravgandhi2411/hinglish-relatedness-sbert`,
`gauravgandhi2411/hinglish-relatedness-benchmark`). The reports were the
anomaly, not this.

## Files are byte-identical copies, deliberately

No headers were prepended and nothing was reformatted. `content/metrics.json`
in the portfolio cites these by **file and line number**, and a provenance
header would shift every line by its own length — silently invalidating the
citations it was meant to document. Origin is recorded here instead.

| file | origin (private `mindmeld`) | origin commit |
|---|---|---|
| `warmer/baseline_report.md` | `generator/evals/reports/baseline_report.md` | `16f35d168e33893bdd62fdee1a18bde6331b88bf` |
| `warmer/baseline_report_pre-fix.md` | same path, **pre-fix state** | `675994dbcfa3eb80540414b379578983be406969` |
| `warmer/embedding_separation_report.md` | `generator/evals/finetune/data/embedding_separation_report.md` | `6cd2c801f4d59f8b31dad5d8114ad4063719ed4e` |
| `warmer/embedding_separation.json` | `generator/evals/finetune/data/embedding_separation.json` | `6cd2c801f4d59f8b31dad5d8114ad4063719ed4e` |

Two versions of `baseline_report.md` are published because the portfolio cites
**the same file at two different commits** — line 24 reads
`Spearman correlation: -0.003` before the fine-tune and `0.813` after. One
snapshot could only ever support one of those two citations.

## What they contain

- **`baseline_report.md`** — the five-dimension eval harness output for the
  shipped Hinglish model: semantic ranking (en and hi-en), cross-language
  consistency, LLM-judged clue quality, rival calibration, and difficulty
  modelling.
- **`embedding_separation_report.md`** — cluster separation for the base vs
  fine-tuned model. Worth reading past the headline: the 9.3x figure holds
  only when both models are scored on the fine-tune's own partition, and the
  control (each model on its own partition) reverses it. Both are reported.
