import re
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Distribution(unittest.TestCase):
    def test_generated_skills_are_complete_and_match_the_source(self):
        for name in ('job-mate-apply', 'job-mate-followup'):
            skill = ROOT / 'skills' / name
            self.assertIn('name: ' + name, (skill / 'SKILL.md').read_text(encoding='utf-8'))
            for source in (ROOT / 'core').glob('*.py'):
                self.assertEqual(source.read_bytes(), (skill / 'scripts' / source.name).read_bytes())
            for source in (ROOT / 'references').glob('*.md'):
                self.assertEqual(source.read_bytes(), (skill / 'references' / source.name).read_bytes())
            for doc in skill.rglob('*.md'):
                for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', doc.read_text(encoding='utf-8')):
                    if not re.match(r'[a-z]+:', link):
                        self.assertTrue((doc.parent / link).is_file(), str(doc) + ': ' + link)
            self.assertEqual((ROOT / 'LICENSE').read_bytes(), (skill / 'LICENSE').read_bytes())

    def test_source_manifest_has_no_private_runtime_data(self):
        # The source list includes new files; deleted v1 files are ignored.
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
