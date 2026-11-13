#!/usr/bin/env python3
"""Synchronize self-contained skill resources; no network or installation side effects."""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = ('job-mate-apply', 'job-mate-followup')


def build(check=False, out=None):
    errors = []
    for name in NAMES:
        target = ROOT / 'skills' / name
        sources = [(p, target / 'scripts' / p.name) for p in (ROOT / 'core').glob('*.py')]
        sources += [(p, target / 'references' / p.name) for p in (ROOT / 'references').glob('*.md')]
        sources += [(ROOT / 'LICENSE', target / 'LICENSE')]
        expected = {str(dst.relative_to(target)) for _, dst in sources}
        for folder in ('scripts', 'references'):
            for p in (target / folder).glob('*'):
                if p.is_file() and str(p.relative_to(target)) not in expected:
                    errors.append(str(p))
                    if not check:
                        p.unlink()
        for src, dst in sources:
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                errors.append(str(dst))
                if not check:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
        if out:
            shutil.copytree(target, Path(out) / name, dirs_exist_ok=True)
    if check and errors:
        raise SystemExit('Generated resources differ: ' + ', '.join(errors))
    print('Two independent skills verified' if check else 'Two independent skills built')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--out')
    args = parser.parse_args()
    build(args.check, args.out)
