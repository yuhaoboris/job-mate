import concurrent.futures
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
sys.path.insert(0, str(ROOT / 'scripts'))
import job_mate as core


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

    def historical_message(self, kind, key, days_ago, **fields):
        return {'kind': kind, 'messageKey': key, 'at': core.stamp(self.time.return_value - days_ago * 86400),
                'text': 'Synthetic message ' + key, **fields}

    def import_history(self, *messages, target=None):
        return self.call('import_history', runId=self.run, target=target or self.target, messages=list(messages),
                         evidence='Synthetic page: confirmed conversation, message direction and timestamps')

    def assertProblem(self, code, op, **kwargs):
        with self.assertRaises(core.Problem) as cm:
            self.call(op, **kwargs)
        self.assertEqual(cm.exception.code, code)

    def test_snapshot_has_no_side_effect(self):
        self.assertFalse(self.call('snapshot')['configured'])
        self.assertFalse(self.root.exists())

    def test_first_use_stages_original_and_preserves_authorization(self):
        self.setup_profile()
        s = self.call('snapshot')
        self.assertTrue(s['configured'])
        self.assertEqual(Path(s['resume']['path']).read_bytes(), self.resume.read_bytes())
        self.assertEqual(s['profile']['authorization'], [])

    def test_minimal_profile_without_resume_or_authorization_prompt(self):
        self.call('configure', revision=0, profile=dict(targetRoles=['Frontend'], locations=['Shanghai']))
        self.run = self.call('begin', revision=1, skill='apply', account='synthetic', accountEvidence='page',
                             actions=['greet'], noSend=False)['result']['id']
        self.screen()
        self.assertIsInstance(self.prepare(), str)

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

    def test_other_job_at_same_company_can_receive_its_first_greeting(self):
        self.begin()
        self.screen()
        attempt = self.prepare()
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='Synthetic greeting delivered')
        self.time.return_value += 20
        self.assertProblem('ALREADY_CONTACTED', 'prepare', runId=self.run, target=self.target,
                           kind='greet', text='Duplicate greeting')
        other_job = dict(self.target, threadKey='visible-2', title='Full stack')
        self.screen(other_job)
        self.assertIsInstance(self.prepare(target=other_job), str)

    def test_apply_resume_requires_current_authorization_even_with_saved_resume_permission(self):
        self.profile['authorization'] = ['greet', 'resume']
        configured = self.setup_profile()
        request = dict(revision=configured['revision'], skill='apply', account='synthetic',
                       accountEvidence='Synthetic account', actions=['resume'])
        self.assertProblem('NOT_AUTHORIZED', 'begin', **request, noSend=False)
        run = self.call('begin', **request, noSend=True)['result']
        digest = self.call('snapshot')['resume']['sha256']
        self.assertProblem('NO_SEND', 'prepare', runId=run['id'], target=self.target,
                           kind='resume', text=digest)

    def test_apply_can_send_confirmed_resume_to_qualified_job_without_inbound_request(self):
        self.begin(actions=['resume'], targets=[self.target['threadKey']])
        digest = self.call('snapshot')['resume']['sha256']
        request = dict(runId=self.run, target=self.target, kind='resume', text=digest)
        self.screen(result='pending')
        self.assertProblem('SCREEN_REQUIRED', 'prepare', **request)
        self.screen()
        self.assertProblem('NOT_AUTHORIZED', 'prepare', runId=self.run, target=self.target,
                           kind='greet', text='Synthetic greeting')
        self.assertProblem('RESUME_MISMATCH', 'prepare', **{**request, 'text': 'different resume hash'})
        attempt = self.prepare('resume', text=digest)
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text=digest)
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='Synthetic attachment delivered')
        self.time.return_value += 20
        self.assertProblem('ALREADY_SENT', 'prepare', **request)

    def test_followup_resume_keeps_the_inbound_request_requirement(self):
        self.begin(skill='followup', actions=['resume'])
        self.call('record', runId=self.run, target=self.target, kind='inbound', text='Synthetic HR message')
        digest = self.call('snapshot')['resume']['sha256']
        self.assertProblem('INVALID_INPUT', 'prepare', runId=self.run, target=self.target,
                           kind='resume', text=digest)
        self.assertIsInstance(self.prepare('resume', text=digest, requestEvidence='HR requested a resume'), str)

    def test_apply_records_greeting_and_resume_as_separate_sends_to_the_same_job(self):
        self.begin(actions=['greet', 'resume'], maxActions=2)
        self.screen()
        greeting = self.prepare()
        self.call('guard', runId=self.run, attemptId=greeting, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=greeting, status='sent',
                  evidence='Synthetic greeting delivered', actualText='Platform greeting')
        self.time.return_value += 20
        digest = self.call('snapshot')['resume']['sha256']
        application = self.prepare('resume', text=digest)
        self.call('guard', runId=self.run, attemptId=application, target=self.target, text=digest)
        self.call('finish', runId=self.run, attemptId=application, status='sent',
                  evidence='Synthetic resume attachment delivered')
        self.call('end', runId=self.run)
        sent = [event for event in self.call('snapshot')['records'][0]['history'] if event.get('status') == 'sent']
        self.assertEqual([(event['kind'], event['text']) for event in sent],
                         [('greet', 'Platform greeting'), ('resume', digest)])

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

    def test_only_successful_new_greetings_count_toward_twenty(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.call('finish', runId=self.run, attemptId=attempt, status='failed_no_send', evidence='not clicked')
        for i in range(20):
            target = dict(self.target, threadKey=str(i))
            self.screen(target)
            attempt = self.prepare(target=target, text='Hello')
            self.call('guard', runId=self.run, attemptId=attempt, target=target, text='Hello')
            self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='visible bubble')
        self.assertProblem('RUN_LIMIT', 'prepare', runId=self.run, target=self.target, kind='greet', text='again')

    def test_explicit_lower_bound_on_send_volume_is_not_required_for_read_only(self):
        self.begin(noSend=True)
        self.call('end', runId=self.run)
        self.assertEqual(self.call('snapshot')['attempts'], [])

    def test_user_specified_send_cap_is_respected(self):
        self.begin(maxActions=1); self.screen(); attempt = self.prepare(text='Hello')
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Hello')
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='bubble')
        other = dict(self.target, threadKey='other')
        self.screen(other)
        self.assertProblem('RUN_LIMIT', 'prepare', runId=self.run, target=other, kind='greet', text='Hello')

    def test_followup_can_cover_more_than_twenty_without_profile(self):
        self.run = self.call('begin', revision=0, skill='followup', noSend=False, actions=['reply'],
                             account='synthetic', accountEvidence='page')['result']['id']
        for i in range(21):
            target = dict(self.target, threadKey=str(i))
            self.call('record', runId=self.run, target=target, kind='inbound', text='Hello')
            attempt = self.prepare('reply', target=target, text='Reply')
            self.call('guard', runId=self.run, attemptId=attempt, target=target, text='Reply')
            self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='visible bubble')

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

    def test_empty_conversation_continues_same_attempt_with_fresh_guard(self):
        self.begin(maxActions=1); self.screen(); attempt = self.prepare()
        # Substitute for an entry click that creates a conversation without sending.
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('record', runId=self.run, kind='observe', target=self.target, observed='info',
                  note='Synthetic page: history loaded, editable empty composer, repeated checks show no sent message')
        self.call('pause', paused=True)
        self.assertProblem('PAUSED', 'guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('pause', paused=False)
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='Synthetic manual message delivered')
        self.call('end', runId=self.run)
        s = self.call('snapshot')
        self.assertEqual(len(s['attempts']), 1)
        self.assertEqual(s['attempts'][0]['status'], 'sent')
        self.assertEqual(s['counts']['waiting'], 1)
        self.assertEqual(s['counts']['uncertain'], 0)

    def test_finished_entry_attempt_cannot_be_reused_for_manual_greeting(self):
        self.begin(); self.screen(); attempt = self.prepare()
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.call('finish', runId=self.run, attemptId=attempt, status='uncertain', evidence='Synthetic entry timed out')
        self.assertProblem('ATTEMPT_CLOSED', 'guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic greeting')
        self.assertProblem('UNRESOLVED_SEND', 'prepare', runId=self.run, target=self.target, kind='greet', text='retry')

    def test_greeting_length_default_boundary_and_explicit_override(self):
        self.begin(); self.screen()
        message = '你好，' + 'A' * 97
        self.assertProblem('GREETING_TOO_LONG', 'prepare', runId=self.run, target=self.target, kind='greet', text=message + '！', maxChars=100)
        self.assertEqual(self.call('snapshot')['attempts'], [])
        for invalid in (0, -1, True, 1.5, 10001):
            self.assertProblem('INVALID_INPUT', 'prepare', runId=self.run, target=self.target, kind='greet', text=message, maxChars=invalid)
        attempt = self.prepare(text=message + '！')
        self.assertNotIn('maxChars', self.call('snapshot')['attempts'][0])
        self.call('finish', runId=self.run, attemptId=attempt, status='failed_no_send', evidence='Synthetic cancellation before click')
        self.time.return_value += 61
        attempt = self.prepare(text=message + '！', maxChars=101)
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text=message + '！')
        # Actual platform text is evidence, not a draft to truncate or reject.
        actual = '平台自动招呼' * 30
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='Synthetic auto message delivered', actualText=actual)
        self.assertEqual(self.call('snapshot')['attempts'][-1]['actualText'], actual)
        self.assertProblem('ATTEMPT_CLOSED', 'guard', runId=self.run, attemptId=attempt, target=self.target, text=message + '！')

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

    def test_history_import_is_chronological_and_idempotent_in_no_send_run(self):
        self.call('stage_resume', source=str(self.resume))
        self.begin(skill='followup', noSend=True, maxActions=0)
        first = self.historical_message('greet', 'first', 9)
        reply = self.historical_message('inbound', 'reply', 5, at='2027-01-10T10:00:00+08:00')
        latest = self.historical_message('outbound', 'latest', 4, text='  Synthetic outgoing\nmessage  ')
        saved = self.import_history(latest, first, reply)
        self.assertEqual(saved['result'], {'imported': 3, 'updated': 0, 'unchanged': 0})
        self.assertEqual(self.import_history(first, latest, reply), {
            'revision': saved['revision'], 'result': {'imported': 0, 'updated': 0, 'unchanged': 3}})
        snapshot = self.call('snapshot')
        record = snapshot['records'][0]
        self.assertEqual(snapshot['attempts'], [])
        self.assertFalse(snapshot['configured'])
        self.assertEqual(record['status'], 'waiting')
        self.assertEqual(record['text'], latest['text'])
        self.assertEqual([row['messageKey'] for row in record['history']], ['first', 'reply', 'latest'])
        self.assertEqual(record['history'][1]['at'], '2027-01-10T02:00:00Z')
        self.assertEqual(record['history'][1]['importedAt'], core.stamp(self.time.return_value))

    def test_history_import_rejects_invalid_data_without_partial_writes(self):
        self.begin(skill='followup', noSend=True, targets=[self.target['threadKey']])
        message = self.historical_message('outbound', 'first', 4)
        args = dict(runId=self.run, evidence='Synthetic evidence', messages=[message])
        self.assertProblem('OUT_OF_SCOPE', 'import_history', target=dict(self.target, threadKey='other'), **args)
        self.assertProblem('ACCOUNT_MISMATCH', 'import_history', target=dict(self.target, account='other'), **args)
        before = self.call('snapshot')
        for at in ('2026-11-01T09:00:00', '2026-02-30T09:00:00Z', '2099-01-01T00:00:00Z'):
            self.assertProblem('INVALID_MESSAGE_TIME', 'import_history', **{
                **args, 'target': self.target, 'messages': [message, dict(message, messageKey='bad', at=at)]})
        for conflict in (dict(message, messageKey='other'), dict(message, text='different')):
            self.assertProblem('HISTORY_CONFLICT', 'import_history', **{
                **args, 'target': self.target, 'messages': [message, conflict]})
        self.assertEqual(self.call('snapshot'), before)

    def test_history_import_backfills_existing_messages_and_counts_each_nudge_once(self):
        self.begin(skill='followup', actions=['nudge'])
        inbound = self.call('record', runId=self.run, target=self.target, kind='inbound',
                            text='Old summary', messageKey='inbound')['result']
        real_inbound = self.historical_message('inbound', 'inbound', 10)
        self.assertEqual(self.import_history(real_inbound)['result']['updated'], 1)
        reread = self.call('record', runId=self.run, target=self.target, kind='inbound',
                           text='Old summary', messageKey='inbound')['result']
        self.assertEqual((reread['id'], reread['at']), (inbound['id'], real_inbound['at']))
        self.import_history(self.historical_message('reply', 'original-reply', 8))
        attempt = self.prepare('nudge', text='Synthetic sent follow-up')
        self.call('guard', runId=self.run, attemptId=attempt, target=self.target, text='Synthetic sent follow-up')
        self.call('finish', runId=self.run, attemptId=attempt, status='sent', evidence='Synthetic sent bubble')
        self.call('end', runId=self.run)
        before = self.call('snapshot')
        sent = before['records'][0]['history'][-1]
        old_ts = sent['at']
        self.time.return_value += 4 * 86400
        self.begin(skill='followup', actions=['nudge'])
        existing = self.historical_message('nudge', 'already-sent', 4, at=old_ts, eventId=sent['id'], text=sent['text'])
        self.assertEqual(self.import_history(existing)['result']['updated'], 1)
        self.assertEqual(self.import_history(existing)['result']['unchanged'], 1)
        self.assertEqual(self.call('snapshot')['attempts'], before['attempts'])
        self.import_history(self.historical_message('nudge', 'manually-sent', 7))
        self.assertIsInstance(self.prepare('nudge'), str)

    def test_nudge_uses_original_time_and_same_time_message_order(self):
        self.begin(skill='followup', actions=['nudge'])
        first = self.historical_message('nudge', 'first', 8)
        second = self.historical_message('nudge', 'second', 6)
        self.import_history(second, first)
        self.import_history(self.historical_message('inbound', 'earlier-reply', 9))
        self.import_history(self.historical_message('inbound', 'later-reply', 6, order=1))
        self.assertProblem('NOT_WAITING', 'prepare', runId=self.run, target=self.target, kind='nudge', text='Blocked')
        self.import_history(self.historical_message('outbound', 'recent-outgoing', 2))

        self.assertIsInstance(self.prepare('nudge'), str)

    def test_guard_rechecks_nudge_after_history_import(self):
        self.begin(skill='followup', actions=['nudge'])
        self.import_history(self.historical_message('greet', 'manual-greeting', 4))
        attempt = self.prepare('nudge', text='Synthetic follow-up')
        self.import_history(self.historical_message('inbound', 'recent-reply', 2))
        self.assertProblem('NOT_WAITING', 'guard', runId=self.run, attemptId=attempt,
                           target=self.target, text='Synthetic follow-up')

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

    def test_unrecognized_database_format_is_not_modified(self):
        self.setup_profile()
        path = self.root / 'job-mate.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('UPDATE workspace SET data=?', (json.dumps({'schemaVersion': -1}),))
        before = path.read_bytes()
        self.assertProblem('VERSION_MISMATCH', 'configure', revision=0, profile=self.profile)
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_configuration_only_one_revision_wins(self):
        self.setup_profile()
        def write():
            cmd = [sys.executable, str(ROOT / 'scripts/job_mate.py'), '--workspace', str(self.root)]
            return json.loads(subprocess.run(cmd, input=json.dumps(dict(op='configure', revision=2, profile=self.profile)), text=True, capture_output=True).stdout)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: write(), range(2)))
        self.assertEqual(sum(x['ok'] for x in outcomes), 1)
        self.assertEqual(self.call('snapshot')['revision'], 3)

    def test_single_skill_works_outside_repository(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build.py'), '--out', str(self.base)], check=True, capture_output=True)
        dest = self.base / 'job-mate'
        result = subprocess.run([sys.executable, str(dest / 'scripts/job_mate.py'), '--workspace', str(self.base / 'unused')], input='{"op":"snapshot"}', text=True, capture_output=True)
        self.assertTrue(json.loads(result.stdout)['ok'])


if __name__ == '__main__': unittest.main()
