# Panel

100 synthetic personas react in parallel to any pasted artifact (landing page,
ad, pricing page, policy text). One strong model synthesizes the 100
reactions into findings and a single highest-leverage edit. Compare mode
reruns the same personas on a revised artifact and shows who flipped.

Built for the Nebius x NVIDIA Global AI Hackathon, Best Apps and Agents
track. Model tiering is the product: Nemotron Nano runs the 100 cheap
reaction calls, Nemotron Super generates personas once offline, Nemotron
Ultra runs the single expensive synthesis call — all through Nebius Token
Factory.

> Status: under active development. This README is a stub; the full setup
> guide, architecture diagram, and model-usage writeup land in the final
> stage.

## Stack

- Backend: Python 3.14, FastAPI
- Frontend: React 18 + Vite, plain JavaScript
- Models: Nebius Token Factory (Nemotron Nano / Super / Ultra)

## Local dev (placeholder, expand in the final stage)

```bash
# backend
python3 -m venv .venv && source .venv/bin/activate   # if venv creation fails with an
                                                       # ensurepip error, apt install
                                                       # python3.14-venv, or fall back to
                                                       # `pip install --user --break-system-packages`
pip install -r requirements.txt
uvicorn api.main:app --reload

# frontend
cd web
npm install
npm run dev
```

## Performance (section 5.2 hour-one measurement)

Measured 2026-10-27 against `data/examples/pricing_v1.txt`, 100 real Nano calls per row:

| Concurrency | Elapsed | p50 latency | p95 latency | 429s |
| --- | --- | --- | --- | --- |
| 10 | 94.9s | 8.6s | 11.8s | 0 |
| 25 | 60.2s | 12.9s | 21.5s | 0 |
| 50 | 35.3s | 14.6s | 20.4s | 0 |

Zero 429s at every level tested, so `MAX_CONCURRENCY=50` (the highest tested) wins — 35.3s for 100 reactions, under the 40s target.

**Correction to section 5.2's stated `max_tokens=400` for Nano:** measured wrong. Nano, like Super and Ultra, spends completion-token budget on hidden reasoning before emitting the Reaction JSON — at `max_tokens=400` essentially every call truncated before reaching content. `max_tokens=3000` (see `panel/react.py`'s `REACTION_MAX_TOKENS`) truncated 0/15 in testing, with observed usage topping out at 2092 tokens. The first unpatched run of `run.py` (before this fix) took 106s for 100 calls and had 4 outright failures, entirely explained by this — `tf_client.chat_json`'s truncation-retry path was firing on nearly every call.

## License

MIT, see [LICENSE](LICENSE).
