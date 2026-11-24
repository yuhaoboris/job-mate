#!/usr/bin/env python3
"""Verify the root skill or export its runtime files for offline installation."""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ('SKILL.md', 'LICENSE', 'NOTICE', 'scripts/job_mate.py',
         'references/boss.md', 'references/greeting.md',
         'references/protocol.md', 'references/report.md')


def build(out=None):
    for name in FILES:
        if not (ROOT / name).is_file():
            raise SystemExit('Missing skill file: ' + name)
    if out:
        target = Path(out).resolve() / 'job-mate'
        if target.exists():
            raise SystemExit('Choose a destination without an existing job-mate directory')
        for name in FILES:
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
    print('job-mate exported' if out else 'job-mate verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='verify the root package (default)')
    group.add_argument('--out', help='export a standalone job-mate directory here')
    build(parser.parse_args().out)
