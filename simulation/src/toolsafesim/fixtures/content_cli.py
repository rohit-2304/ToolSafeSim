"""Content CLI.

  python -m toolsafesim.fixtures.content_cli validate [path ...]   # check files (default: all)
  python -m toolsafesim.fixtures.content_cli merge incoming.yaml emails.yaml
        # validate a new batch (e.g. AI-written) and append it to a content file, then re-freeze packs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .content import CONTENT_DIR
from .content_schema import CONTENT_FILES
from .validate import scan_secrets


def _dumper():
    def rep(d, s):
        return d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)
    yaml.SafeDumper.add_representer(str, rep)


def check_rows(name: str, rows: list, existing: list | None = None) -> list[str]:
    model, errs = CONTENT_FILES[name], []
    seen = {yaml.safe_dump(e, sort_keys=True) for e in (existing or [])}
    texts = set()
    for i, r in enumerate(rows):
        try:
            m = model.model_validate(r)
        except Exception as e:  # pydantic ValidationError
            errs.append(f"{name}[{i}]: {str(e).splitlines()[0:4]}")
            continue
        blob = yaml.safe_dump(r)
        errs += [f"{name}[{i}]: {x}" for x in scan_secrets(blob, name)]
        main = max((v for v in m.model_dump().values() if isinstance(v, str)), key=len)
        if main in texts or yaml.safe_dump(r, sort_keys=True) in seen:
            errs.append(f"{name}[{i}]: duplicate of an existing/other entry")
        texts.add(main)
    return errs


def validate_all(content_dir: Path = CONTENT_DIR) -> list[str]:
    errs = []
    for name in CONTENT_FILES:
        rows = yaml.safe_load((content_dir / name).read_text(encoding="utf-8")) or []
        errs += check_rows(name, rows)
    return errs


def merge(incoming: Path, target: str, content_dir: Path = CONTENT_DIR) -> list[str]:
    path = content_dir / target
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    rows = yaml.safe_load(incoming.read_text(encoding="utf-8")) or []
    errs = check_rows(target, rows, existing)
    if errs:
        return errs
    _dumper()
    path.write_text(yaml.safe_dump(existing + rows, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    m = sub.add_parser("merge")
    m.add_argument("incoming", type=Path)
    m.add_argument("target", choices=list(CONTENT_FILES))
    a = ap.parse_args(argv)
    errs = validate_all() if a.cmd == "validate" else merge(a.incoming, a.target)
    for e in errs:
        print("ERROR", e)
    if not errs and a.cmd == "merge":
        from .build import main as rebuild
        print("merged; re-frozen packs:", rebuild()["packs"].keys())
    print("OK" if not errs else f"{len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
