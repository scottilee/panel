# Feedback: building on Nemotron 3 via Nebius Token Factory

Submitted alongside **Panel**, a 1,000-persona synthetic focus group for
marketing copy. Every number below was measured against the live Token
Factory API between 2026-10-27 and 2026-10-28, on
`NVIDIA-Nemotron-3-Nano-30B-A3B`, `nemotron-3-super-120b-a12b` and
`Nemotron-3-Ultra-550b-a55b`. Where a finding changed the code, the file is
named so it can be checked.

The app's shape made it an unusually hard test of one thing in particular:
we issue between 100 and 1,000 structured-output calls per run, so any
failure mode with a 10% incidence shows up every single time, and anything
that silently doubles a call's cost is immediately visible in the bill and
the clock. Most of what follows is about that.

---

## 1. The headline finding: budget consumption is stochastic, and failures bill in full

All three models spend completion-token budget on hidden reasoning before
emitting any content. How much is not a function of the prompt — the same
prompt at the same `max_tokens` succeeds on one call and truncates with an
empty `content` on the next. Measured on Nano, one fixed ~5k-token prompt,
**10 trials per budget**:

| `max_tokens` | Succeeded | Completion tokens on success | On failure |
| --- | --- | --- | --- |
| 8,000 | 3/10 | 3.3-6.8k | 7/7 consumed the full budget |
| 16,000 | 3/10 | 6.0-11.5k | 7/7 consumed the full budget |
| 24,000 | 5/10 | 6.3-16.2k | 5/5 consumed the full budget |
| 48,000 | 7/10 | 4.1-8.9k | 3/3 consumed the full budget |

Two things matter here, and they pull in opposite directions.

**A higher ceiling genuinely improves the success rate** — 3/10 to 7/10
across this range. Look at the success column to see why: a call that
needs 16k tokens emits *nothing at all* at 8k, because content only
begins once reasoning ends. There is no partial answer to detect.

**But every failure bills for the entire budget.** Across all 25 failures
at all four levels, each one consumed essentially its whole ceiling. So
raising `max_tokens` buys a better success rate at the cost of a
proportionally more expensive failure: a failed call at 48k costs six
times a failed call at 8k and takes ~195s against ~75s.

We retry truncations at a doubled budget, capped at two retries
(`panel/tf_client.py`), which is the balance those two facts imply.

**A correction worth recording**, since it shows how easily this behaviour
misleads: we first ran this probe at **3 trials** per budget and got 0/3
at 24,000, which looked like strong evidence that a bigger ceiling made
things *worse* — plausible, since a reasoning model might expand its
thinking to fill the space offered. We changed our retry strategy on that
basis. At 10 trials the same budget is the second best of the four. The
variance here is wide enough that three samples per cell will confidently
tell you the opposite of the truth, and we would not have caught it
without re-running.

**What would have helped most:** populating
`completion_tokens_details.reasoning_tokens`. The OpenAI-compatible schema
has the field; Token Factory returned `None` for it on every call we made,
across all three models. With it, an integrator can see reasoning cost
directly, distinguish "needed a little more" from "never terminated", and
size budgets from data instead of from a 10-trial experiment they had to
build themselves. Per-model distributions in the docs would have saved us
roughly a day.

## 2. Budget guidance from measured truncation, never from visible text

Three times we sized `max_tokens` by estimating prompt + expected output,
and three times we were badly wrong, in both directions:

| Call | First guess | Measured correct value | Why the guess failed |
| --- | --- | --- | --- |
| Nano reaction (6 short fields) | 400 | **3,000** | ~100% truncation at 400; observed peak usage 2,092 |
| Super persona batch (10 personas) | 8,000 | **16,000** | ~4.5k content + ~3.4k reasoning sat right on the line |
| Nano batch digest | 24,000 | **8,000**, after fixing the batch size | At 10 items a batch, 8,000 succeeds 10/10; at 50 it fails 10/10 at any budget — see §5 |

The second row is the expensive one. At 8,000 the call *usually* worked,
so the only symptom was speed: every call that crossed the line truncated,
got silently retried at double, and a 900-persona generation run paced at
**110 minutes instead of 25**. Nothing in the output said why. Sitting just
below the line costs roughly 2.3× in wall-clock and tokens versus sitting
comfortably above it, and it is invisible unless you instrument the retry.

**What would have helped:** per-model published guidance on typical
reasoning-token consumption for structured output, and a response field
that makes a budget-driven retry visible without the integrator adding
their own logging.

## 3. These models cannot count over a list, and asking is actively destructive

We hit this twice, from two directions, and the second time was worse than
the first.

**Ultra, synthesizing over 100 reactions.** Asked for `affected_pct` —
"what share of the panel does this finding affect" — it was off by 12-70
percentage points on one artifact and by as much as 82 across three. We
then retried with the exact discrepancy spelled out in the prompt and got
*identical wrong numbers* back. It is not estimating badly, it is not
counting at all.

Our fix generalizes, and we think it is the right pattern for anyone
putting these models behind a number a user will read: have the model
propose **which** set matters as a machine-checkable filter, and compute
the quantity in code. In our schema `Finding` carries both
`affected_segment` (prose, the model's judgment) and
`affected_segment_filter` (a list of `{field, op, value}` clauses), and
Python recounts the percentage over the real population. Every number
displayed is counted; the model only decides what to count.

**Condensing a batch of 50 reactions.** We asked for recurring themes,
the persona IDs expressing each, *and* a count, ordered most-common-first.
On a live run one shard spent **32,000 completion tokens — every rung of
our retry ladder — and emitted zero content**, discarding half the panel's
data. (This was Super, but we later established the batch size was doing
most of the damage, not the model — see §4.) Dropping the count but still asking for
*every* ID per theme was no better: no truncation, but the stage ran over
eight minutes, because exhaustively classifying 50 items into 3 categories
is the same long serial task in a different costume.

What works is asking for themes plus **2-5 examples**, capped in the
schema, and printing no quantity at all.

**The generalizable lesson:** a request that implies serial bookkeeping
over a list — count it, sort by count, assign every item — doesn't degrade
gracefully into an approximate answer. It triggers runaway reasoning and
returns *nothing*. Decompose so the model does classification and judgment
while the host program does arithmetic and ordering.

## 4. We blamed a model for a batch-size problem, twice

This section was originally titled "model size does not predict fitness
for a task" and reported the opposite conclusion. Keeping the reversal in
is the most useful thing we can offer, because the mistake took ten
minutes to make and hours to catch.

We needed a model to condense a batch of reactions into themes. "Summarize
a batch" reads like mid-tier work, so Super got it. It performed badly, so
we raced it against Nano on the same 50-reaction batch, once each:

| Model | Time | Themes | Required quotes field |
| --- | --- | --- | --- |
| Nano 30B | 83s | 10 | 5 ✓ |
| Super 120B | 157s | 13 | **0 ✗** |

Twice as slow and silently dropping a required field. We moved the job to
Nano and wrote up "the smaller model is better at this" as a finding.

Then we fixed an unrelated problem — the batch was too large (§5) — and
re-ran the comparison **paired, 10 trials, at the new batch size of 10**:

| Model | Succeeded | Median | Themes | Quotes |
| --- | --- | --- | --- | --- |
| Nano 30B | 9/10 | 54s | 5.1 | 5.2 |
| **Super 120B** | **10/10** | **38s** | **7.8** | **5.6** |

Super is better on reliability, latency *and* richness. The original
comparison had measured a batch-size problem and attributed it to the
model — and because a single trial of each is well inside the variance
documented in §1, even the direction of the latency result was luck. The
job is back on Super.

**Two generalizable lessons**, one about these models and one about
evaluating them:

- **Model comparisons are not portable across configurations.** Super's
  disadvantage at 50 items a batch was real and vanished at 10. If you
  benchmark two tiers to pick one, benchmark at the configuration you will
  actually ship, and re-benchmark when that configuration changes.
- **Pair the trials and run enough of them.** Our final comparison gives
  both models the identical batches, so the result does not depend on
  which one drew easier inputs. With single unpaired trials we got a
  confident answer that was wrong in direction, not just in magnitude.

**What would have helped:** task-shaped guidance rather than size-shaped.
"Super is bigger" is true and predicted nothing here either way. Something
like "the larger models tolerate longer inputs per unit of reasoning, so
the smaller model's advantage narrows as batches shrink" would have
pointed us at batch size on day one instead of at the model.

## 5. Shorter inputs reduce runaway reasoning

Once we knew runaway reasoning was the failure mode, we measured its
incidence against input length. Same prompt shape, same 8,000 budget, four
trials each, real reaction data:

| Reactions per call | Succeeded | Median latency | Usable themes returned |
| --- | --- | --- | --- |
| 50 | **0/10** | 61s | — |
| 25 | 3/10 | 65s | 11.7 |
| 10 | **10/10** | 46s | 5.0 |
| 5 | **10/10** | 31s | 1.2 |

This is the strongest effect we found anywhere, and the most actionable:
the failure is not really about the token ceiling at all, it is about how
much the model is asked to hold at once. At 50 items a batch the call
*never* succeeded, at any budget we tried. At 10 it always did — and was
faster per call. The usual "batch it up to save round trips" instinct is
exactly backwards here; we shard at 10 and run 50 concurrently rather than
batching at 50 and running 5.

The themes column is why we did not go smaller still. At 5 items a batch
the calls all succeed and return almost nothing usable — too few people
for anything to qualify as a recurring theme — so "succeeded" and "useful"
are separate axes and 10 is where both hold.

Confirmed in production, end to end on a 200-persona run:

| Design | Calls truncated | Shards lost | Total run |
| --- | --- | --- | --- |
| Super, 50 per shard | 14 | 2 of 4 | 721s |
| Nano, 50 per shard | 14 | 2 of 4 | 321s |
| Nano, 10 per shard | 0 | 0 of 20 | 181s |
| **Super, 10 per shard** | **0** | **0 of 20** | **130s** |

Five and a half times faster, and only the small-batch rows read the whole
panel. The clearest way to put it: the model that was *worst* at 50 items a
batch is the *best* at 10. Batch size was doing all the work, and it had
been masquerading as a difference in model quality (§4).

## 6. Instruction-following degrades on count constraints

`nemotron-3-super-120b-a12b`, asked for exactly 10 personas per call,
intermittently returned 1 or 2 with `finish_reason: "stop"` — a complete,
well-formed response that simply ignored the count. Roughly 5-8% of calls
across a 90-call run. Because the response is well-formed, no schema
validation or truncation retry catches it; the caller has to check the
length itself and retry. A `minItems`/`maxItems` constraint on a JSON
schema array is not enforced.

Two smaller instances of the same thing:

- Asked to pick values from an enumerated set it had never been shown,
  Ultra invented plausible-looking members: `age_band` values of `"50-59"`
  and `"70+"` where the real bands were `50-64` and `65+`. The fix was to
  put the legal values in the prompt *and* show the derived column in the
  data — our persona table had only raw ages. Models can't copy an
  enumeration they've never seen.
- Asked for evidence as bare ID strings (`"p042"`), Ultra returned
  decorated ones (`'p042: "it doesn\'t say the price"'`), and a retry
  asking for bare IDs produced the same decoration again. We normalize
  with a regex rather than retrying.

**Pattern worth documenting:** schema conformance and instruction
conformance are different things, and `response_format` only buys the
first. Anything expressible as a schema constraint rather than prose is
worth moving into the schema — we capped a list length there and the
repair retry then carries the reason automatically.

## 7. What worked well, unprompted

Credit where it is due, because the above is a list of problems:

- **Concurrency.** 50 concurrent Nano calls, sustained, **zero 429s** at
  every level we tested (10/25/50). 100 reactions in 35.3 seconds. We
  budgeted a day for rate-limit handling and needed almost none of it.
- **The OpenAI-compatible surface is genuinely drop-in.** `AsyncOpenAI`
  pointed at `api.tokenfactory.nebius.com/v1`, and `response_format` with
  a `json_schema` worked first try on all three models. We wrote a
  code-fence-stripping fallback defensively and it has never fired on the
  happy path.
- **Reaction quality at the small end.** A 30B model holding a specific
  persona for six short fields, 1,000 at a time, was the bet the whole app
  rested on, and it holds up: the reactions read like different people,
  not one voice with different labels.
- **`seed` is honoured** and does reduce run-to-run variance, though not
  to determinism (expected at temperature > 0).

## 8. Concrete requests, in priority order

1. **Populate `completion_tokens_details.reasoning_tokens`.** It is the
   single thing that would most improve the integration experience. It
   turns every finding in §1 and §2 from archaeology into a metric.
2. **Publish per-model reasoning-token distributions** for structured
   output, or at minimum a documented warning that budget consumption is
   stochastic and that `finish_reason: "length"` with empty content is a
   distinct, non-budget failure.
3. **Document the counting limitation** with the recommended decomposition
   (model proposes the filter, host computes the number). We arrived at it
   twice by accident and it would be a strong entry in a best-practices
   guide.
4. **Enforce, or document as unenforced, JSON-schema array bounds**
   (`minItems`/`maxItems`). Silent violation with `finish_reason: "stop"`
   is the hardest class of failure to detect.
5. **Task-shaped model selection guidance** rather than size-shaped, per
   §4.

---

## Reproducing this

The two tables that drive our design decisions — §1 (budget) and §5 (input
length) — come from a harness in this repository:

```bash
python scripts/probe_nemotron.py --trials 10
```

It calls the live API and provokes failures on purpose, so it costs real
credits and is kept out of the test suite. Each attempt is measured on its
own terms, bypassing our retry ladder, which is what makes the failure
columns meaningful.

The production behaviour these findings informed is in
`panel/tf_client.py` (retry strategy), `panel/synthesize.py` (sharding,
condensing, recounting) and `panel/personas.py` (generation budget and
retry), each documented inline with the measurement that produced it.
