import concurrent.futures
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))
import job_mate as core
from migrate import migrate


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        self.resume = self.base / 'resume.txt'
        self.resume.write_text('Synthetic candidate: two years of frontend experience.', encoding='utf-8')
        self.target = dict(platform='boss', account='synthetic', company='Example Ltd', title='Frontend', threadKey='visible-1')
        self.profile = dict(candidateFacts='resume.txt: two years frontend', targetRoles=['Frontend'], locations=['Shanghai'], salary=None, requirements=[], exclusions=['outsourcing'], authorization=[], confirmation='Synthetic user confirmed')
        self.clock = patch.object(core, 'now', return_value=1800000000)
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)

    def call(self, op, **kw):
        if op == 'record' and kw.get('kind') == 'inbound':
            kw.setdefault('messageKey', 'visible-message-' + kw.get('text', ''))
        return core.execute(self.root, dict(op=op, **kw))

    def setup_profile(self):
        self.call('stage_resume', source=str(self.resume))
        return self.call('configure', revision=1, profile=self.profile)

    def begin(self, skill='apply', noSend=False, actions=None, **kwargs):
        if not self.root.exists():
            self.setup_profile()
        result = self.call('begin', revision=self.call('snapshot')['revision'], skill=skill, account='synthetic', accountEvidence='Visible synthetic account', noSend=noSend, actions=actions or ([] if noSend else ['greet']), authorizationEvidence='Synthetic user explicitly permits the selected actions', **kwargs)
        self.run = result['result']['id']
        return result

    def screen(self, target=None, result='qualified'):
        return self.call('record', runId=self.run, kind='screen', target=target or self.target, result=result, reasons=['Visible JD and synthetic resume match'])

    def prepare(self, kind='greet', target=None, **kw):
        return self.call('prepare', runId=self.run, target=target or self.target, kind=kind, text=kw.pop('text', 'Synthetic greeting'), **kw)['result']['id']

    def assertProblem(self, code, op, **kwargs):
        with self.assertRaises(core.Problem) as cm:
            self.call(op, **kwargs)
        self.assertEqual(cm.exception.code, code)

    def test_snapshot_has_no_side_effect(self):
        self.assertFalse(self.call('snapshot')['configured'])
        self.assertFalse(self.root.exists())

    def test_first_use_stages_original_and_requires_confirmation(self):
        self.setup_profile()
        s = self.call('snapshot')
        self.assertTrue(s['configured'])
        self.assertEqual(Path(s['resume']['path']).read_bytes(), self.resume.read_bytes())
        self.assertEqual(s['profile']['authorization'], [])
        changed = copy.deepcopy(self.profile); changed.pop('confirmation')
        self.assertProblem('INVALID_INPUT', 'configure', revision=s['revision'], profile=changed)

    def test_stale_profile_does_not_overwrite(self):
        self.setup_profile()
        self.assertProblem('STALE_REVISION', 'configure', revision=1, profile=self.profile)
        self.assertEqual(self.call('snapshot')['revision'], 2)

    def test_no_send_overrides_permission(self):
        self.begin(noSend=True, actions=['greet']); self.screen()
        self.assertProblem('NO_SEND', 'prepare', runId=self.run, target=self.target, kind='greet', text='hello')
        self.assertEqual(self.call('snapshot')['attempts'], [])

    def test_read_only_followup_does_not_require_irrelevant_preferences(self):
        self.call('stage_resume', source=str(self.resume))
        self.begin(skill='followup', noSend=True)
        self.call('record', runId=self.run, target=self.target, kind='inbound', text='Synthetic HR question')
        self.assertEqual(len(self.call('snapshot')['records']), 1)
        self.assertFalse(self.call('snapshot')['configured'])

    def test_scope_and_account(self):
        self.begin(targets=['other'])
        self.assertProblem('OUT_OF_SCOPE', 'prepare', runId=self.run, target=self.target, kind='greet', text='hello')
        wrong = dict(self.target, account='other')
        self.assertProblem('ACCOUNT_MISMATCH', 'record', runId=self.run, target=wrong, kind='inbound', text='x')

    def test_qualified_screen_is_required(self):
        self.begin(); self.screen(result='pending')
        self.assertProblem('SCREEN_REQUIRED', 'prepare', runId=self.run, target=self.target, kind='greet', text='hello')

    def test_send_lifecycle_and_no_double_finish(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.assertProblem('GUARD_REQUIRED', 'finish', runId=self.run, attemptId=attempt, status='sent', evidence='visible message')
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='visible sent bubble')
        self.assertProblem('ATTEMPT_CLOSED', 'finish', runId=self.run, attemptId=attempt, status='sent', evidence='again')
        self.call('end', runId=self.run)
        self.assertEqual(self.call('snapshot')['counts']['waiting'], 1)

    def test_guard_rechecks_content_target_and_pause(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.assertProblem('CONTENT_CHANGED', 'guard', runId=self.run, attemptId=attempt, target=self.target, text='changed')
        self.call('pause', paused=True)
        self.assertProblem('PAUSED', 'guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=attempt, status='failed_no_send', evidence='not clicked')

    def test_unknown_end_blocks_new_send_until_reconciled(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.call('end', runId=self.run)
        self.assertEqual(self.call('snapshot')['counts']['uncertain'], 1)
        self.begin(); self.screen()
        self.assertProblem('UNRESOLVED_SEND', 'prepare', runId=self.run, target=self.target, kind='greet', text='retry')
        self.call('end', runId=self.run)
        self.call('reconcile', attemptId=attempt, status='sent', evidence='visible original sent message')
        self.begin(); self.screen(); self.time.return_value += 61
        self.assertProblem('ALREADY_CONTACTED', 'prepare', runId=self.run, target=self.target, kind='greet', text='retry')

    def test_explicit_recovery_preserves_attempt(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.assertProblem('RUN_ACTIVE', 'begin', revision=self.call('snapshot')['revision'], skill='apply', account='synthetic', accountEvidence='x', noSend=True)
        self.call('recover_run', runId=self.run, evidence='Synthetic executor has stopped')
        self.assertEqual(self.call('snapshot')['attempts'][0]['id'], attempt)
        self.assertEqual(self.call('snapshot')['attempts'][0]['status'], 'uncertain')

    def test_profile_and_resume_cannot_change_during_run(self):
        self.begin()
        self.assertProblem('RUN_ACTIVE', 'configure', revision=self.call('snapshot')['revision'], profile=self.profile)
        self.assertProblem('RUN_ACTIVE', 'stage_resume', source=str(self.resume))

    def test_new_resume_needs_fact_review(self):
        self.setup_profile()
        self.call('stage_resume', source=str(self.resume))
        self.assertProblem('ONBOARDING_REQUIRED', 'begin', revision=self.call('snapshot')['revision'], skill='apply', account='synthetic', accountEvidence='x', noSend=False)

    def test_run_limit_counts_attempts(self):
        self.begin(maxActions=1); self.screen(); attempt = self.prepare()
        self.call('finish', runId=self.run, attemptId=attempt, status='failed_no_send', evidence='not clicked')
        self.time.return_value += 61
        self.assertProblem('RUN_LIMIT', 'prepare', runId=self.run, target=self.target, kind='greet', text='again')

    def test_commitment_requires_exact_approved_text(self):
        self.begin(skill='followup', actions=['reply'])
        self.call('record', runId=self.run, kind='inbound', target=self.target, text='Interview Wednesday?')
        d = self.call('decide', runId=self.run, target=self.target, question='Accept Wednesday?')['result']
        self.assertProblem('DECISION_REQUIRED', 'prepare', runId=self.run, target=self.target, kind='reply', text='yes', commitment=True)
        self.call('answer', revision=self.call('snapshot')['revision'], decisionId=d['id'], answer='yes', approvedText='Wednesday 3 pm works.')
        self.assertProblem('DECISION_REQUIRED', 'prepare', runId=self.run, target=self.target, kind='reply', text='Wednesday works.', commitment=True, decisionId=d['id'])
        attempt = self.prepare('reply', text='Wednesday 3 pm works.', commitment=True, decisionId=d['id'])
        self.assertIsInstance(attempt, str)

    def test_auto_greeting_records_actual_text(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='visible auto greeting', actualText='Platform default greeting')
        e = self.call('snapshot')['records'][0]['history'][-1]
        self.assertEqual(e['text'], 'Platform default greeting')
        self.assertEqual(e['plannedText'], 'Synthetic greeting')

    def test_reconcile_preserves_actual_text_and_consumes_decision(self):
        self.begin(skill='followup', actions=['reply'])
        self.call('record', runId=self.run, kind='inbound', target=self.target, text='Interview?')
        d = self.call('decide', runId=self.run, target=self.target, question='Interview?')['result']
        self.call('answer', revision=self.call('snapshot')['revision'], decisionId=d['id'], answer='yes', approvedText='Approved words')
        a = self.prepare('reply', text='Approved words', commitment=True, decisionId=d['id'])
        self.call('guard', runId=self.run, attemptId=a, target=self.target, text='Approved words')
        self.call('finish', runId=self.run, attemptId=a, status='uncertain', evidence='disconnected', actualText='Approved words')
        self.call('end', runId=self.run)
        self.call('reconcile', attemptId=a, status='sent', evidence='visible message')
        self.assertEqual(next(x for x in self.call('snapshot')['decisions'] if x['id'] == d['id'])['status'], 'resolved')

    def test_reconcile_actual_auto_greeting_and_failed_send_dedup(self):
        self.begin(); self.screen(); a = self.prepare()
        self.call('finish', runId=self.run, attemptId=a, status='uncertain', evidence='disconnected', actualText='Actual platform text')
        self.call('end', runId=self.run)
        self.call('reconcile', attemptId=a, status='failed_no_send', evidence='definitely not sent')
        self.assertEqual(self.call('snapshot')['records'][0]['text'], 'Actual platform text')
        self.time.return_value += 61
        self.begin(); self.screen()
        self.assertIsInstance(self.prepare(), str)

    def test_scope_covers_records_and_decisions(self):
        self.begin(targets=['only-other'])
        self.assertProblem('OUT_OF_SCOPE', 'record', runId=self.run, target=self.target, kind='inbound', text='x')
        self.assertProblem('OUT_OF_SCOPE', 'decide', runId=self.run, target=self.target, question='x')

    def test_interview_state_survives_neutral_reply(self):
        self.begin(skill='followup', actions=['reply'])
        self.call('record', runId=self.run, kind='inbound', target=self.target, text='Interview?')
        self.call('record', runId=self.run, kind='observe', target=self.target, observed='interview_invited')
        a = self.prepare('reply', text='I will confirm availability.')
        self.call('guard', runId=self.run, attemptId=a, target=self.target, text='I will confirm availability.')
        self.call('finish', runId=self.run, attemptId=a, status='sent', evidence='visible message')
        self.assertEqual(self.call('snapshot')['counts']['interview'], 1)

    def test_duplicate_inbound_is_idempotent(self):
        self.begin(skill='followup', noSend=True)
        first = self.call('record', runId=self.run, kind='inbound', target=self.target, text='old message')
        self.time.return_value += 4 * 86400
        second = self.call('record', runId=self.run, kind='inbound', target=self.target, text='old message')
        self.assertEqual(first['result']['id'], second['result']['id'])
        self.assertEqual(len(self.call('snapshot')['events']), 1)
        self.assertEqual(first['result']['ts'], second['result']['ts'])

    def test_revoking_approval_invalidates_prepared_send(self):
        self.begin(skill='followup', actions=['reply'])
        self.call('record', runId=self.run, kind='inbound', target=self.target, text='Interview?')
        d = self.call('decide', runId=self.run, target=self.target, question='Interview?')['result']
        self.call('answer', revision=self.call('snapshot')['revision'], decisionId=d['id'], answer='yes', approvedText='Approved words')
        a = self.prepare('reply', text='Approved words', commitment=True, decisionId=d['id'])
        self.call('pause', paused=True)
        self.call('answer', revision=self.call('snapshot')['revision'], decisionId=d['id'], answer='Cancel that approval', approvedText='')
        self.call('pause', paused=False)
        self.assertProblem('DECISION_CHANGED', 'guard', runId=self.run, attemptId=a, target=self.target, text='Approved words')

    def test_damaged_database_is_not_empty_workspace(self):
        self.root.mkdir(); (self.root / 'job-mate.sqlite3').write_bytes(b'broken')
        with self.assertRaises(sqlite3.DatabaseError): self.call('snapshot')
        self.assertEqual((self.root / 'job-mate.sqlite3').read_bytes(), b'broken')

    def test_legacy_marker_is_never_overwritten(self):
        self.root.mkdir(); (self.root / 'job-mate.json').write_text('{"schemaVersion":1}')
        self.assertProblem('LEGACY_WORKSPACE', 'stage_resume', source=str(self.resume))
        self.assertFalse((self.root / 'job-mate.sqlite3').exists())

    def test_concurrent_configuration_only_one_revision_wins(self):
        self.setup_profile()
        def write():
            cmd = [sys.executable, str(ROOT / 'core/job_mate.py'), '--workspace', str(self.root)]
            return json.loads(subprocess.run(cmd, input=json.dumps(dict(op='configure', revision=2, profile=self.profile)), text=True, capture_output=True).stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: write(), range(2)))
        self.assertEqual(sum(x['ok'] for x in outcomes), 1)
        self.assertEqual(self.call('snapshot')['revision'], 3)

    def test_single_skill_works_outside_repository(self):
        import shutil
        for name in ('job-mate-apply', 'job-mate-followup'):
            dest = self.base / name
            shutil.copytree(ROOT / 'skills' / name, dest)
            result = subprocess.run([sys.executable, str(dest / 'scripts/job_mate.py'), '--workspace', str(self.base / 'unused')], input='{"op":"snapshot"}', text=True, capture_output=True)
            self.assertTrue(json.loads(result.stdout)['ok'])
            for file in (dest / 'references').glob('*.md'): self.assertNotIn('../../', file.read_text())

    def test_migration_preserves_original_and_unknown_sends(self):
        source = self.base / 'legacy'; (source / 'ledger').mkdir(parents=True)
        (source / 'job-mate.json').write_text('{"schemaVersion":1}')
        (source / 'profile.json').write_text('{"authorization":{"apply":{"send":true}}}')
        row = dict(self.target, kind='greet', status='uncertain', text='old greeting', at='2026-01-01T00:00:00Z')
        (source / 'ledger/events.jsonl').write_text(json.dumps(row)+'\n')
        before = (source / 'ledger/events.jsonl').read_bytes()
        migrate(source, self.root)
        state = self.call('snapshot')
        self.assertEqual((source / 'ledger/events.jsonl').read_bytes(), before)
        self.assertTrue(state['paused']); self.assertFalse(state['configured'])
        self.assertEqual(state['attempts'][0]['status'], 'uncertain')
        self.assertEqual(state['counts']['uncertain'], 1)

    def test_migration_bad_line_does_not_publish_partial_workspace(self):
        source = self.base / 'legacy'; (source / 'ledger').mkdir(parents=True)
        (source / 'job-mate.json').write_text('{"schemaVersion":1}')
        (source / 'profile.json').write_text('{}')
        (source / 'ledger/events.jsonl').write_text('{broken')
        with self.assertRaises(ValueError): migrate(source, self.root)
        self.assertFalse(self.root.exists())


if __name__ == '__main__': unittest.main()
