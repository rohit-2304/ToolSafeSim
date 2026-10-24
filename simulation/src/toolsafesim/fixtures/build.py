"""Build frozen fixture packs to simulation/fixtures/*.json with a checksum manifest.

Usage: python -m toolsafesim.fixtures.build
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import billing, files, injections, productivity, repo, web
from .common import INTERNAL_DOMAIN

FIXTURE_VERSION = "1"
PACKS = {
    "productivity_demo_v1": productivity.build,
    "billing_demo_v1": billing.build,
    "repo_demo_v1": repo.build,
    "web_demo_v1": web.build,
    "files_demo_v1": files.build,
}
OUT_DIR = Path(__file__).resolve().parents[3] / "fixtures"


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(out: Path = OUT_DIR, seed: int = 0) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"fixture_version": FIXTURE_VERSION, "seed": seed, "internal_domain": INTERNAL_DOMAIN, "packs": {}}
    for name, fn in PACKS.items():
        text = _dump(fn(seed))
        (out / f"{name}.json").write_text(text, encoding="utf-8")
        manifest["packs"][name] = hashlib.sha256(text.encode()).hexdigest()
    inj = _dump({"injections": injections.INJECTIONS, "decoys": injections.DECOYS, "slots": injections.SLOTS})
    (out / "injections.json").write_text(inj, encoding="utf-8")
    manifest["packs"]["injections"] = hashlib.sha256(inj.encode()).hexdigest()
    (out / "manifest.json").write_text(_dump(manifest), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
