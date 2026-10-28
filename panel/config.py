"""Env loading, model IDs, constants. Section 2.

tf_client.py's env() is the actual env reader (repo-root .env fallback) --
this module aggregates the named constants the rest of the codebase needs
rather than re-implementing env loading a second way.

This is the single definition of each model ID and of MAX_CONCURRENCY.
Both used to be declared here *and* re-read from env in each module that
wanted them (react, personas, synthesize, compare, scripts/gen_personas),
with the default string retyped every time -- and they had already
drifted: MAX_CONCURRENCY read 50 here and 25 in react.py, which was the
value that actually capped the fan-out, while .env.example, the README's
performance table and infra/variables.tf all documented 50.

Deliberately NOT re-declared here: the artifact length and persona-count
bounds. Those are enforced by pydantic Field constraints on RunRequest in
schemas.py, which is the contract module and stays free of imports from
the rest of the package; a second copy here would just be a second thing
to forget to update.
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
# Overridable because a container's image filesystem is the wrong place
# to write run records: on Cloud Run it's ephemeral and in-memory, and a
# read-only filesystem would make every run fail at the persist step.
# Point it at /tmp there (see the Dockerfile).
RUNS_DIR = Path(env("RUNS_DIR") or (DATA_DIR / "runs"))
EXAMPLES_DIR = DATA_DIR / "examples"
CACHED_DIR = DATA_DIR / "cached"
WEB_DIST_DIR = ROOT / "web" / "dist"
