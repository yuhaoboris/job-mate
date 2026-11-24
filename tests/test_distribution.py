import re
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Distribution(unittest.TestCase):
    def test_generated_skills_are_complete_and_match_the_source(self):
        self.assertFalse(list((ROOT / 'skills').glob('*/SKILL.md')))
        self.assertIn('name: job-mate', (ROOT / 'SKILL.md').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([sys.executable, str(ROOT / 'scripts/build.py'), '--out', out], check=True, capture_output=True)
            skill = Path(out) / 'job-mate'
            self.assertEqual({p.name for p in skill.iterdir()}, {'SKILL.md', 'LICENSE', 'NOTICE', 'references', 'scripts'})
            for p in skill.rglob('*'):
                if p.is_file():
                    self.assertEqual(p.read_bytes(), (ROOT / p.relative_to(skill)).read_bytes())
            for doc in skill.rglob('*.md'):
                for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', doc.read_text(encoding='utf-8')):
                    if not re.match(r'[a-z]+:', link):
                        self.assertTrue((doc.parent / link).is_file(), str(doc) + ': ' + link)
            self.assertEqual((ROOT / 'LICENSE').read_bytes(), (skill / 'LICENSE').read_bytes())

    def test_source_manifest_has_no_private_runtime_data(self):
        # Check tracked and untracked files that still exist.
        listed = subprocess.check_output(['git', 'ls-files', '-c', '-o', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
        forbidden = {'job-mate.sqlite3', 'profile.json', 'candidate.md', '.env'}
        for item in listed:
            p = ROOT / item
            if not p.exists():
                continue
            self.assertNotIn(p.name, forbidden, item)
            self.assertNotIn('.private', p.relative_to(ROOT).parts, item)
            self.assertFalse(p.is_symlink(), item)


if __name__ == '__main__': unittest.main()
