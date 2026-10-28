"""Env loading, model IDs, constants. Section 2.

tf_client.py's env() is the actual env reader (repo-root .env fallback,
needed before this module existed) -- this module just aggregates the
named constants api/main.py and run.py need, rather than re-implementing
env loading a second way.
"""

from __future__ import annotations

from pathlib import Path

from panel.tf_client import env

ROOT = Path(__file__).resolve().parent.parent

MODEL_NANO = env("MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
MODEL_SUPER = env("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b")
MODEL_ULTRA = env("MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")

DEFAULT_PERSONA_COUNT = int(env("PERSONA_COUNT", "100"))
MAX_CONCURRENCY = int(env("MAX_CONCURRENCY", "50"))

TAVILY_API_KEY = env("TAVILY_API_KEY")

# "false" runs fan_out in-process (default, used for the live demo); "true"
# dispatches to the Nebius Serverless Job in jobs/react_job.py (stage 9).
USE_SERVERLESS_JOB = env("USE_SERVERLESS_JOB", "false").lower() == "true"

DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
EXAMPLES_DIR = DATA_DIR / "examples"
CACHED_DIR = DATA_DIR / "cached"
WEB_DIST_DIR = ROOT / "web" / "dist"

MIN_ARTIFACT_LENGTH = 50
MAX_ARTIFACT_LENGTH = 20_000
MIN_PERSONA_COUNT = 10
