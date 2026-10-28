# Panel

**Find out who you're losing, and why — before you ship.**

Paste a landing page, ad, pricing page, or policy notice. A panel of
distinct synthetic people — 100 by default, up to 1,000 — reads it in
parallel and reacts the way they actually would in the first twenty seconds:
bored, confused, suspicious, indifferent. One stronger model reads every
reaction and tells you the three things that are costing you, which segment
each one affects, and one specific edit to try. Apply the edit and it reruns
the same people so you can see who changed their mind.

A real 100-person copy test costs thousands of dollars and takes a week.
This costs about ten cents and takes a minute: 35 seconds for the panel to
read, the rest for the synthesis.

**97 of 100 personas say "I wouldn't buy this." That's the point.** The
panel is deliberately built to be hard to please (see
[Anti-sycophancy](#anti-sycophancy-why-the-panel-says-no)) — a panel that
tells you your copy is great is worth nothing.

Built for the Nebius x NVIDIA Global AI Hackathon, Best Apps and Agents
track.

---

## Model tiering is the product

Panel needs a hundred cheap opinions and one expensive one. That shape is
only affordable because one open model family ships in three sizes behind a
single API.

```mermaid
flowchart LR
    A["Artifact<br/>(text or URL)"] --> B["Nemotron Nano<br/>N parallel calls<br/>one per persona"]
    B --> C["N Reactions<br/>sentiment · would_act<br/>confusion · objection"]
    C --> D["Nemotron Ultra<br/>1 call<br/>synthesis"]
    D --> E["3 findings<br/>+ 1 highest-leverage edit"]
    E -->|"apply edit"| A

    C -. "N > 150 only" .-> H["Nemotron Super<br/>1 call per 10<br/>condense to themes"]
    H -.-> D

    F["Nemotron Super<br/>offline, run once"] -.->|"generates"| G[("1,000 personas<br/>data/personas.json")]
    G -.-> B

    subgraph TF["Nebius Token Factory"]
        B
        D
        F
        H
    end
```

| Model | Role | Why this size |
| --- | --- | --- |
| **Nemotron Nano** (`NVIDIA-Nemotron-3-Nano-30B-A3B`) | One reaction call per persona per run (100–1,000) | The work is "stay in character, react in one sentence, fill six short fields." Cheap and fast matters far more than depth — and it's the call that happens N× per run. |
| **Nemotron Super** (`nemotron-3-super-120b-a12b`) | Writes the 1,000-persona pool, once, offline. Above N=150, condenses the reactions into themes, 10 at a time | Persona quality is a one-time cost every future run inherits, so it's worth a mid-size model. The condensing job we twice assigned wrongly before measuring properly — it left for Nano and came back (see [what we learned](#what-we-learned-building-this)); paired over 10 trials at the shipped batch size, Super wins on reliability, latency and richness alike. |
| **Nemotron Ultra** (`Nemotron-3-Ultra-550b-a55b`) | 1 synthesis call per run | Reads the reactions (or, at large N, the themes condensed from them) plus a persona table and has to find the non-obvious pattern, name the affected segment, and quote the artifact verbatim. This is the one call where reasoning depth is worth paying for. |

At N ≤ 150 Ultra reads the raw reactions directly; above it, Super makes
one extra pass to condense them first, because 1,000 raw reactions is a
400k-token prompt. That split is the whole reason a 1,000-person panel
costs well under a dollar.

### Nebius services used

- **Token Factory** — all inference. Every Nano, Super, and Ultra call goes
  through `api.tokenfactory.nebius.com` via the OpenAI-compatible API. This
  is the project's qualifying runtime dependency and is not optional or
  incidental: there is no local model and no other provider.
- **Hosting:** Google Cloud Run rather than Nebius AI Cloud. Stated plainly
  because the submission checklist asks: we have Token Factory credits but
  not AI Cloud credits, and Serverless Endpoint hosting for a CPU-only web
  app would have been real out-of-pocket cost for an app that mostly sits
  idle between judging sessions. Inference is 100% Nebius either way.

---

## Setup

```bash
# 1. clone, then set your key
cp .env.example .env
#    put your Token Factory key in TF_API_KEY

# 2. backend deps
pip install -r requirements.txt
#    (if `python3 -m venv .venv` fails with an ensurepip error:
#     apt install python3.14-venv, or use
#     pip install --user --break-system-packages)

# 3. build the frontend
cd web && npm install && npm run build && cd ..

# 4. run it — serves the API and the built UI on one port
uvicorn api.main:app --port 8000
#    open http://localhost:8000
```

For frontend development with hot reload, run `npm run dev` in `web/` in a
second terminal (it proxies API calls to port 8000). Add `?mock=1` to the URL
to drive the whole UI from synthetic data with zero API calls and zero cost —
useful for working on the interface without spending credits.

Try it from the command line instead:

```bash
python run.py --artifact data/examples/pricing_v1.txt --n 100
```

Run the tests with `make test` — the whole suite is offline, so it costs
nothing and makes no model calls.

A note on the deployed configuration: Cloud Run serves the app to
`allUsers`, because judges need a link rather than credentials, and every
run spends real credits. `MAX_PERSONA_COUNT`, `MAX_CONCURRENT_RUNS` and
`RATE_LIMIT_RUNS` (see `.env.example` and `infra/variables.tf`) bound what
one caller can spend; the defaults are generous for a person and useless
for a script. They are unset by default locally.

---

## Anti-sycophancy: why the panel says no

An LLM asked to role-play a customer will flatter you by default. That makes
a synthetic panel worthless. Three things push against it:

1. **The personas are built skeptical.** Generation requires at least 3 of
   every 10 personas to have skepticism ≥ 70/100 — the shipped pool lands
   at 386 of 1,000 — and the pool spans the full 1-100 range on seven
   numeric traits (tech comfort, skepticism, and the Big Five).

   The pool is also exactly stratified and globally unique, which the
   generator checks and fails on: **125 personas per region** across 8
   regions, **200 per age band** across 5, **200 per income band** across
   5, **zero duplicate names** in 1,000, and no occupation held by more
   than **0.9%** of the pool. Verify with
   `python -c "from panel.personas import *; print(len(find_duplicate_names(load_personas())))"`.
2. **The reaction prompt forbids helpfulness.** It asks for the real first-20-seconds
   reaction, tells the model indifference is an acceptable answer, and
   defaults `would_act` to false unless something specifically moved them.
3. **It's verified, not assumed.** `scripts/gen_personas.py` reacts the
   personas to all three example artifacts and fails the build unless mean
   sentiment lands within [-0.8, +0.8] and at least 25% of them say
   `would_act: false` on every artifact. Measured on the shipped
   1,000-persona pool (a fixed 150-persona sample — 3,000 Nano calls to
   pin down a number a few hundred already settles to within a point):

   | Artifact | Mean sentiment | `would_act: false` |
   | --- | --- | --- |
   | `pricing_v1.txt` | -0.28 | 146/150 (97%) |
   | `ad_v1.txt` | -0.48 | 147/150 (98%) |
   | `policy_v1.txt` | -0.30 | 150/150 (100%) |

   Passed on the first attempt, with no forced-skepticism rewrites.

---

## Performance

Measured against `data/examples/pricing_v1.txt`, 100 real Nano calls per row:

| Concurrency | Elapsed | p50 latency | p95 latency | 429s |
| --- | --- | --- | --- | --- |
| 10 | 94.9s | 8.6s | 11.8s | 0 |
| 25 | 60.2s | 12.9s | 21.5s | 0 |
| 50 | 35.3s | 14.6s | 20.4s | 0 |

Zero 429s at every level tested, so `MAX_CONCURRENCY=50` — **100 reactions in
35.3 seconds.** A full run including synthesis lands around 40-90s depending
on how long Ultra reasons.

Above N=150 a condensing pass is added. Measured end to end at N=200:

| Condensing design | Shards lost | Condense + synthesis | Total |
| --- | --- | --- | --- |
| Super, 50 per shard | 2 of 4 | 644s | 721s |
| Nano, 50 per shard | 2 of 4 | 239s | 321s |
| Nano, 10 per shard | 0 of 20 | 97s | 181s |
| **Super, 10 per shard** (shipped) | **0 of 20** | **48s** | **130s** |

Five and a half times faster than where it started, and only the
small-batch rows read the whole panel rather than half of it. All of that
came from the batch size, not the model — note that the same model which
was worst at 50 per shard is best at 10
(see [what we learned](#what-we-learned-building-this) — getting this
wrong twice is the most instructive thing in the project).

Extrapolating to a full 1,000-persona panel: ~6 minutes of reading, ~1
minute of condensing (100 calls in two waves of 50), plus the synthesis —
roughly 8 minutes, against an 1,800s request timeout. The default stays at
100 because 35 seconds is a different kind of experience.

---

## What we learned building this

The non-obvious findings, because they shaped the architecture. The
model-behaviour ones are written up properly, with full measurement
tables, in [FEEDBACK.md](FEEDBACK.md) — and the two that drove design
decisions are reproducible with `python scripts/probe_nemotron.py`.

- **All three Nemotron models spend completion-token budget on hidden
  reasoning before emitting content, and how much is stochastic.** The spec
  we wrote assumed `max_tokens=400` was plenty for a six-field JSON
  reaction. In practice essentially every call truncated before reaching
  content. The same prompt at the same budget succeeded on one call and
  truncated with zero content on an immediate retry. Fix: a measured
  budget (`REACTION_MAX_TOKENS=3000`, observed usage peaking at 2092) plus
  treating `finish_reason == "length"` as a distinct retry case from
  "content didn't validate," since they need opposite responses.
- **A token budget that *usually* fits is the expensive kind of wrong.**
  Persona generation ran at `max_tokens=8000` against a prompt needing ~4.5k
  of content and ~3.4k of reasoning. It worked — every truncated call was
  silently retried at double the budget — so the only symptom was speed: a
  900-persona run paced at 110 minutes instead of 25. Sitting *near* the
  ceiling costs about 2.3× the tokens and wall-clock of sitting well above
  it, and the retry that hides the problem is what makes it invisible.
- **Don't ask an LLM to count.** Ultra's `affected_pct` claims were off by
  12-70 percentage points on the pricing example and by as much as 82
  across the three, and a retry with the exact discrepancy spelled
  out produced identical wrong numbers. So Ultra proposes *which* segment
  matters as a structured filter, and Python computes the percentage from
  the actual reactions. Every number in the findings panel is counted, not
  estimated.
- **…and asking anyway isn't merely unreliable, it's destructive.** We
  learned the same lesson a second time writing the shard digests, and it
  took two tries to stop asking. v1 wanted a theme, the persona IDs
  expressing it, *and* a count, ordered most-common-first: on a real
  200-person run one shard spent 32,000 completion tokens — every rung of
  the retry ladder — reasoning about those counts and emitted **zero**
  content, discarding half the panel's data. v2 dropped the count but still
  asked for *every* ID under each theme: no truncation, but the stage ran
  for over eight minutes, because exhaustively classifying 50 reactions into
  three categories is the same long serial task wearing a different hat.
  v3 asks for themes plus **2-5 examples**, capped in the schema, and prints
  no quantity at all. Every number in the report comes from recounting
  Ultra's segment filter against the real personas — which is where this
  project's numbers came from in the first place.
- **The obvious tier for a job isn't always the right one.** "Condense a
  batch of reactions" reads like mid-tier work, so Super got it. Raced
  head-to-head against Nano on the same real 50-reaction shard: Nano
  finished in 83s with 10 themes and the 5 required quotes; Super took 157s,
  returned 13 themes and **zero** quotes, and on live runs exhausted the
  whole truncation ladder (12k → 24k → 48k) on some shards. We moved the job
  to Nano. Super keeps the work it's genuinely needed for — writing the
  persona pool offline, where quality compounds into every run that follows.
  Ten minutes of measurement overturned an architecture we'd have otherwise
  presented as a design principle.
- **A graceful degradation path is worth building before you need it.**
  That failed shard didn't fail the run — `_digest_shard` returns `None`,
  the label tells Ultra it's seeing 100 of 200 reactions, and the report
  that came out was still coherent and correctly segmented. Every Nano call
  was already paid for by the time digesting starts, so "lose a shard" has
  to beat "lose the run."
- **Personas ask for things text can't deliver.** Early reaction data was
  full of "show me a video," "add a screenshot," "link a live dashboard" —
  unanswerable by any copy edit, so they poisoned the edit suggestions. The
  reaction prompt now requires a change to the words on the page.
- **Synthetic people are hard to persuade, and that's honest.** One good
  edit flips roughly 0-7 of 100, not the 20+ we hoped. We tested
  single-phrase edits, multi-theme rewrites, and edits targeting the single
  most-requested change; the ceiling held. We report the real number.

---

## Honest limitations

- **Directional, not a replacement for real research.** These are language
  models role-playing demographics, not humans. Use it to catch the
  confusions and objections you can't see in your own copy, not to forecast
  conversion rates.
- **One edit moves few people.** See above — expect a few percent. The
  diagnosis is the value; the flip count is a sanity check on it.
- **Run state is in memory.** A server restart loses in-flight runs, and the
  deployment must be pinned to a single instance or SSE reconnects will land
  on an instance that doesn't know the run.
- **Compare requires identical persona counts** on both runs, enforced in
  the UI by locking the control once a compare starts.
- **The Tavily URL-extraction path is unverified.** We had no key; the
  `requests` + `readability-lxml` fallback is what's tested.
- **A 1,000-person run takes about six minutes**, because throughput is
  concurrency-bound (50 concurrent Nano calls) and the panel size is the
  numerator. The default stays at 100 for that reason; the slider goes to
  1,000 when the question is worth the wait.
- **Occupations repeat in the pool, names don't.** All 1,000 full names are
  unique and that's enforced; occupations are a measured target rather than
  a gate, because 1,000 people genuinely do include more than one nurse.
  732 of 1,000 are distinct, and the most common one is 9 people (0.9% of
  the pool) — against 5 of 100 (5.0%) before the scale-up.
- **The pool over-represents unusual jobs.** Telling the generator to avoid
  stock occupations worked, and then kept working: six lighthouse keepers
  and nine funeral directors per thousand people is not what a real
  population looks like. It makes the crowd feel varied, which is what the
  grid needs, but don't read the occupation mix as demographically faithful.
- **Some traits cluster despite the prompt.** Tech comfort spreads across 63
  distinct values in 1,000 personas; extraversion only 30, most of them
  between 38 and 55. The model rounds to familiar numbers no matter how
  firmly it's asked not to, so the Big Five traits carry less signal at the
  edges than skepticism and tech comfort do.

---

## License

MIT, see [LICENSE](LICENSE).
