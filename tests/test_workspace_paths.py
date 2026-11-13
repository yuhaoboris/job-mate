import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import job_mate as core


class WorkspacePaths(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('JOB_MATE_HOME', None)

    def test_defaults_and_explicit_override_priority(self):
        with patch.object(core.Path, 'home', return_value=self.base):
            self.assertEqual(core.resolve_workspace(), self.base / '.job-mate')
            self.assertEqual(core.resolve_workspace(project_dir=self.project), self.project / 'job-mate')
            os.environ['JOB_MATE_HOME'] = str(self.base / 'existing')
            self.assertEqual(core.resolve_workspace(project_dir=self.project), self.base / 'existing')
            self.assertEqual(core.resolve_workspace(self.base / 'chosen', self.project), self.base / 'chosen')

    def test_invalid_project_does_not_fall_back(self):
        for project in ('relative-project', self.base / 'missing'):
            with self.subTest(project=project), self.assertRaises(core.Problem) as error:
                core.resolve_workspace(project_dir=project)
            self.assertEqual(error.exception.code, 'INVALID_PROJECT')

    def test_each_installed_skill_uses_chat_project_independent_of_cwd(self):
        resume = self.base / 'synthetic.txt'
        resume.write_text('Synthetic resume', encoding='utf-8')
        for name in ('job-mate-apply', 'job-mate-followup'):
            project = self.project / name
            project.mkdir()
            script = ROOT / 'skills' / name / 'scripts/job_mate.py'
            command = [sys.executable, str(script), '--project-dir', str(project)]
            def call(request):
                result = subprocess.run(command, input=json.dumps(request), cwd=script.parent,
                                        text=True, capture_output=True, check=True)
                return json.loads(result.stdout)
            snapshot = call({'op': 'snapshot'})
            workspace = project / 'job-mate'
            self.assertEqual(snapshot['workspace'], str(workspace))
            self.assertFalse(workspace.exists())
            call({'op': 'stage_resume', 'source': str(resume)})
            self.assertTrue((workspace / 'job-mate.sqlite3').is_file())
            # Pin later calls to the returned workspace, even with another project supplied.
            command += ['--workspace', snapshot['workspace']]
            command[command.index('--project-dir') + 1] = str(self.base)
            stored = call({'op': 'snapshot'})
            self.assertEqual(stored['workspace'], snapshot['workspace'])
            saved = Path(stored['resume']['path'])
            self.assertEqual(saved.parent, workspace / 'resumes')
            self.assertEqual(saved.read_bytes(), resume.read_bytes())
