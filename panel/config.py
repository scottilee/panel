"""Model IDs, panel sizing and paths: the single definition of each.

tf_client.env() is the env reader (with a repo-root .env fallback); this
module only names the constants the rest of the codebase needs. Artifact
length and persona-count bounds live on the request models in schemas.py.
"""

from __future__ import annotations

from pathlib import Path

from panel.tf_client import env

ROOT = Path(__file__).resolve().parent.parent

MODEL_NANO = env("MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
MODEL_SUPER = env("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b")
MODEL_ULTRA = env("MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")

DEFAULT_PERSONA_COUNT = int(env("PERSONA_COUNT", "100"))
# Measured 2026-10-27 against pricing_v1.txt: zero 429s at 10/25/50
# concurrent Nano calls, 100 reactions in 35.3s at 50.
MAX_CONCURRENCY = int(env("MAX_CONCURRENCY", "50"))

DATA_DIR = ROOT / "data"
# Overridable because the image filesystem is the wrong place for run
# records in a container; the Dockerfile points this at /tmp.
RUNS_DIR = Path(env("RUNS_DIR") or (DATA_DIR / "runs"))
EXAMPLES_DIR = DATA_DIR / "examples"
CACHED_DIR = DATA_DIR / "cached"
WEB_DIST_DIR = ROOT / "web" / "dist"
