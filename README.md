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

## License

MIT, see [LICENSE](LICENSE).
