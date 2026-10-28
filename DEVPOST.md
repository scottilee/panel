# Devpost submission copy

Paste-ready text for each Devpost field. Numbers here are measured, not
estimated — see README.md for the tables and FEEDBACK.md for the
model-behaviour work.

---

## Project name

**Panel**

## Tagline (one line)

Find out who you're losing, and why — before you ship.

## Elevator pitch (short description)

Paste a landing page, ad, or pricing page. A hundred distinct synthetic
people — up to a thousand if you want — read it and react the way they actually would in the first twenty
seconds — bored, confused, suspicious, indifferent. Then one much larger
model reads their reactions and tells you the three things costing you
customers, which segment each one affects, and one specific edit to try.

---

## What it does

Panel is a synthetic focus group for anything you're about to publish.

You paste in copy — a landing page, an ad, a pricing table, a policy
notice. Pick how many people should read it, from 10 to 1,000. Hit run.

What happens next is the part worth watching: a grid of tiles fills in
live, one per person, each turning a colour as that persona's reaction
lands. At the default 100 people, you have a verdict in about a minute:

- **A headline finding** with a counted number attached, e.g. "30% of
  lower-income, high-skepticism readers react negatively, mostly on price
  with no proof of savings." Each finding also shows how many people that
  segment is and what share of the panel it makes up.
- **Three findings ranked by severity**, each naming the affected segment
  and citing the specific people whose objections support it. Click a
  segment and the grid filters to exactly the readers in it.
- **One highest-leverage edit** — a verbatim phrase from your copy and a
  proposed replacement.
- **Who you're winning and who you're losing**, in a sentence each.

Then the part that makes it a tool rather than a demo: **Apply and
compare**. One click rewrites that phrase, reruns the *same*
people against the edited copy, and shows you who changed their mind and
who didn't — per person, as a delta. Not a prediction. A rerun.

Every persona is a specific individual with an age, region, income band,
occupation, household, seven numeric personality traits (tech comfort,
skepticism, and the Big Five), concrete values and pet peeves, and a voice
describing how they personally read marketing. Click any tile to read them.

**The counterintuitive part: 97 of 100 say "I wouldn't buy this," and
that's the product.** A panel that tells you your copy is great is worth
nothing. See below.

## The problem it solves

A real 100-person copy test costs thousands of dollars and takes a week, so
almost nobody does one — they ship and guess. The people who most need the
feedback (a solo founder, a small nonprofit, a city writing a parking
notice) are exactly the people who can't buy it.

Panel costs about ten cents and takes a minute.

But the reason to build it on an open model family specifically is sharper
than cost. The job needs a *hundred to a thousand* cheap opinions and *one*
expensive judgement, in the same request. That shape is only affordable
because one model family ships in three sizes behind a single API.

## How we built it

**Nebius Token Factory for every inference call**, across all three sizes
of NVIDIA's Nemotron 3:

| Model | Job | Calls per run |
| --- | --- | --- |
| **Nemotron Nano 30B** | Each persona's reaction: stay in character, react in one sentence, fill six short fields | 100-1,000 |
| **Nemotron Super 120B** | Writes the 1,000-persona pool offline. At large N, condenses reactions into themes | 0 (or ~100 above N=150) |
| **Nemotron Ultra 550B** | The synthesis: find the non-obvious pattern, name the segment, quote the copy verbatim | 1 |

Python 3.14 / FastAPI with SSE streaming, React 18 + Vite, Pydantic schemas
as the contract between them, Docker, and Terraform onto Google Cloud Run.

**Three design decisions did most of the work:**

**1. Every finding's number is counted in Python, never by a model.** Ultra proposes
*which* segment a finding affects as a machine-checkable filter
(`{field, op, value}` clauses); Python counts how many personas match it
and what share of them actually reacted negatively. We did this because Ultra's own percentage claims were
off by 12-70 points, and retrying with the discrepancy spelled out
produced identical wrong numbers. Each finding's percentage and segment
size are counts, and the prompt bars the headline and claims from citing
any other figure. When a filter can't be recounted (it names a band that
doesn't exist, say), the card is labelled an estimate rather than shown as
a count.

**2. The panel is built to be hard to please.** Persona generation requires
at least 3 in every 10 to have skepticism ≥70/100, and the build *fails*
unless the pool stays within bounds on three example artifacts. Measured on
the shipped pool: 97%, 98% and 100% say they wouldn't act.

**3. Compare mode reruns rather than predicts.** Both runs use the same
personas with the same per-persona seeds, so the difference is the edit
rather than a different sample of people. Model sampling still adds some
noise: an unchanged rerun moved 4-7 people in 100 at our earlier
temperature of 0.7, and the compare view shows that scale next to every
flip count. `scripts/measure_noise.py` re-measures it at the shipped 0.4.

## Challenges we ran into

**The honest answer: almost all of it was one thing.** All three Nemotron
models spend completion-token budget on hidden reasoning before emitting
any content, and how much is *stochastic*. The same prompt at the same
`max_tokens` succeeds on one call and returns nothing on the next.

That single fact cost us:

- A reaction budget of 400 tokens that truncated ~100% of calls (→ 3,000).
- A persona-generation budget that *usually* worked, so the only symptom
  was speed: a 900-persona run paced at **110 minutes instead of 25**
  because every borderline call silently retried at double. Nothing in the
  output said why.
- An entire condensing stage that had to be designed three times.

We measured our way out: a harness
(`scripts/probe_nemotron.py`) that runs the same prompt 10 times per
configuration. It is in the repository because the measurements matter more
than our conclusions did — **twice it proved us wrong about something we
had already shipped**:

- We reported that a larger token ceiling made truncation *worse*, on three
  trials. At ten trials it is clearly better (3/10 → 7/10). We had changed
  production code on the bad sample.
- We moved a job off Super onto Nano after a head-to-head where Nano was
  twice as fast. At ten paired trials, at the batch size we actually ship,
  Super wins on every axis. The original comparison had measured a
  *batch-size* problem and blamed the model.

Both reversals are written up in FEEDBACK.md rather than quietly
corrected, because a judge re-running the harness should find it agrees
with us.

**The other recurring theme:** these models cannot count over a list, and
asking them to is not merely unreliable, it is destructive. One request for
"themes, the IDs expressing each, and a count, most common first" made a
model spend 32,000 tokens reasoning and emit **zero** content, discarding
half the panel's data. The fix was deleting the count field. The IDs are
the evidence; Python does the arithmetic.

## Accomplishments we're proud of

- **100 people in 35 seconds, measured**, with zero rate-limit errors at 50
  concurrent calls at every level we tested — and a thousand in roughly
  eight minutes for well under a dollar (extrapolated from the measured
  N=200 run).
- **A 5.5× speedup on the condensing stage** (721s → 130s at N=200), all of
  it from measurement that contradicted our instincts.
- **1,000 personas with zero duplicate names**, exact demographic
  stratification (125 per region, 200 per age band and income band), and no
  occupation held by more than 0.9% of the pool.
- **Building the anti-sycophancy in as a gate, not a hope.** The persona
  generator fails its own build if the panel gets too agreeable.
- **Saying the uncomfortable number out loud.** One good edit flips roughly
  0-7 people in 100, not the 20+ we hoped. We show the real count and
  reframed the product around diagnosis rather than persuasion, because the
  alternative was a number we knew was flattering.

## What we learned

The full write-up is in FEEDBACK.md. The three that generalize beyond this
project:

1. **Size a reasoning model's token budget from measured truncation, never
   from prompt-plus-output.** We got this wrong three times. A budget that
   *usually* fits is the expensive kind of wrong, because the retry that
   hides it also doubles the bill, and nothing surfaces it.
2. **Let the model decide what to count and let your code do the
   counting.** Any request implying serial bookkeeping over a list doesn't
   degrade into an approximate answer — it returns nothing at all.
3. **Benchmark at the configuration you ship, with paired trials, enough
   times.** Single unpaired trials gave us answers that were wrong in
   *direction*, not just magnitude — twice.

And one about the product rather than the models: **the honest result was
more useful than the flattering one.** When we found that one edit moves
only a handful of people, the temptation was to find an edit that moved
more. What actually made the tool worth using was reframing it: it tells
you *who you're losing and why*, which is the part you cannot see in your
own copy. The flip count is a sanity check on the diagnosis, not the
product.

## What's next

- **Longer artifacts.** Today it reads up to 20,000 characters of a single
  page. Reading a whole funnel — ad, landing page, checkout — and finding
  where people drop out between them is the obvious next step.
- **Persona pools you bring yourself.** The generator is already
  parameterised by demographic slice; pointing it at a real customer base's
  distribution instead of a synthetic-but-balanced one would make the
  segments map onto segments you actually sell to.
- **Tracking an edit across versions,** so you can see whether the thing
  you fixed stayed fixed.

## Built with

`python` · `fastapi` · `pydantic` · `server-sent-events` · `asyncio` ·
`react` · `vite` · `javascript` · `docker` · `terraform` ·
`google-cloud-run` · `nebius-token-factory` · `nvidia-nemotron-3` ·
`nemotron-nano` · `nemotron-super` · `nemotron-ultra`

## Links

- Live demo: *(fill in after `make deploy`)*
- Repository: *(fill in)*
- Demo video: *(fill in)*

---

## Notes for filming the demo

The run order that shows the most in the least time:

1. **Open with the grid empty and the slider at 100.** Say what it is in one
   sentence while clicking the "Pricing page v1" example.
2. **Hit run and don't talk over it.** The tiles filling in live is the
   thing people remember; give it ten seconds of silence.
3. **Read the headline aloud**, then click one finding's segment chip so the
   grid visibly filters to that cohort. That is the moment it stops looking
   like a text generator.
4. **Click a single tile** and read two lines of that persona's voice. Makes
   the thousand people feel like people.
5. **Apply and compare.** Show the delta view: who flipped, who didn't.
   Say the real number out loud — it is more convincing than a big one.
6. **If there is time:** drag the slider to 1,000 and let it start, just to
   show the 32-column grid. Don't wait for it to finish on camera.

Worth saying on camera, because it is the thing judges will wonder about:
the panel disagrees with you by design, and 97 of 100 saying "no" is the
measured baseline, not a failure of the copy.
