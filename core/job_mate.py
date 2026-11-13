#!/usr/bin/env python3
"""job-mate 2.0: offline workspace protocol, Python standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

VERSION = '2.1.0'
SCHEMA = 2
ACTIONS = {'greet', 'reply', 'nudge', 'resume'}
RESULTS = {'sent', 'failed_no_send', 'uncertain'}
OBSERVATIONS = {'interview_invited', 'interview_decided', 'rejected', 'job_closed', 'declined', 'silent', 'replied', 'info'}
TERMINAL = {'rejected', 'job_closed', 'declined'}
MAX_RESUME = 5 * 1024 * 1024


class Problem(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise Problem(code, message)


def text(value, label, required=True, limit=100000):
    require(isinstance(value, str) and len(value) <= limit and (not required or value.strip()), 'INVALID_INPUT', label + '需要有效文字')
    return value.strip()


def strings(value, label, required=False):
    require(isinstance(value, list) and len(value) <= 100, 'INVALID_INPUT', label + '需要列表')
    result = [text(x, label, limit=1000) for x in value]
    require(not required or result, 'INVALID_INPUT', label + '不能为空')
    return list(dict.fromkeys(result))


def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, 'INVALID_INPUT', label + '超出范围')
    return value


def boolean(value, label):
    require(type(value) is bool, 'INVALID_INPUT', label + '需要 true/false')
    return value


def now():
    return time.time()


def stamp(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now() if ts is None else ts))


def uid(prefix):
    return prefix + '-' + uuid.uuid4().hex


def fresh():
    return {'schemaVersion': SCHEMA, 'version': VERSION, 'revision': 0, 'profile': None,
            'resume': None, 'account': None, 'paused': False, 'run': None,
            'events': [], 'attempts': [], 'decisions': [], 'createdAt': stamp()}


def profile(value):
    require(isinstance(value, dict), 'INVALID_PROFILE', '资料必须是对象')
    require(set(value) <= {'candidateFacts', 'targetRoles', 'locations', 'salary', 'requirements', 'exclusions', 'authorization', 'confirmation'}, 'INVALID_PROFILE', '资料含未知字段')
    p = {k: strings(value.get(k), k, k in ('targetRoles', 'locations')) for k in ('targetRoles', 'locations', 'requirements', 'exclusions')}
    p['candidateFacts'] = text(value.get('candidateFacts'), '有来源的个人事实')
    p['confirmation'] = text(value.get('confirmation'), '本人确认依据', limit=10000)
    s = value.get('salary')
    if s is not None:
        require(isinstance(s, dict) and set(s) == {'min', 'max', 'currency', 'period', 'basis'}, 'INVALID_PROFILE', '薪资需要 min/max/currency/period/basis，或 null 表示不设门槛')
        require(all(type(s[k]) in (int, float) and 0 <= s[k] <= 100000000 for k in ('min', 'max')) and s['max'] >= s['min'], 'INVALID_PROFILE', '薪资区间无效')
        require(s['currency'] in ('CNY', 'HKD', 'USD') and s['period'] in ('month', 'year') and s['basis'] in ('gross', 'net', 'unspecified'), 'INVALID_PROFILE', '薪资口径无效')
    p['salary'] = s
    auth = value.get('authorization', [])
    p['authorization'] = strings(auth, '长期发送授权')
    require(set(auth) <= ACTIONS, 'INVALID_PROFILE', '发送授权类型无效')
    return p


def identity(value, account):
    require(isinstance(value, dict), 'INVALID_TARGET', '需要页面目标')
    require(value.get('account') == account, 'ACCOUNT_MISMATCH', '页面账号与本轮账号不符')
    require(value.get('platform', 'boss') == 'boss', 'INVALID_TARGET', '当前只支持 BOSS')
    return {k: text(value.get(k), k, limit=1000) for k in ('account', 'company', 'title', 'threadKey')} | {'platform': 'boss'}


def contacted(state, entry):
    if entry['kind'] == 'inbound':
        return True
    if entry['kind'] not in ACTIONS:
        return False
    attempt = next((a for a in state['attempts'] if a['id'] == entry.get('attemptId')), None)
    status = attempt['status'] if attempt else entry.get('status')
    return status != 'failed_no_send'


def same(a, b):
    return all(a.get(k) == b.get(k) for k in ('platform', 'account', 'threadKey'))


def event(state, kind, target, **fields):
    row = {'id': uid('event'), 'at': stamp(), 'ts': now(), 'kind': kind, **target, **fields}
    state['events'].append(row)
    return row


def decision(state, target, question, key=None):
    for d in state['decisions']:
        if key and d.get('key') == key and d['status'] == 'open':
            return d
    d = {'id': uid('decision'), 'at': stamp(), 'target': target, 'question': question, 'status': 'open', 'key': key}
    state['decisions'].append(d)
    return d


def active(state, req):
    run = state['run']
    require(run and req.get('runId') == run['id'], 'RUN_MISMATCH', '任务已结束或不是本轮任务')
    return run


def expected(state, req):
    require(type(req.get('revision')) is int and req['revision'] == state['revision'], 'STALE_REVISION', '资料已变化，请重读并核对，不要盲目重试')


def summary(state, root):
    rows = {}
    for e in state['events']:
        key = (e.get('platform'), e.get('account'), e.get('threadKey'))
        if not all(key):
            continue
        row = rows.setdefault(key, {k: e.get(k) for k in ('platform', 'account', 'threadKey', 'company', 'title')})
        row.setdefault('history', []).append(e)
        row['at'] = e.get('at')
        row['text'] = e.get('text', e.get('note', ''))
        old = row.get('status')
        if e['kind'] in ACTIONS:
            row['status'] = 'uncertain' if e.get('status') not in ('sent', 'failed_no_send') else ('waiting' if e['status'] == 'sent' else 'failed_no_send')
        elif e['kind'] == 'screen' and old not in ('waiting', 'uncertain', 'replied', 'interview', 'closed'):
            row['status'] = e['result']
        elif e['kind'] == 'inbound':
            row['status'] = 'replied'
        elif e['kind'] == 'observe':
            if e['observed'] in TERMINAL:
                row['status'] = 'closed'
            elif e['observed'] in ('interview_invited', 'interview_decided'):
                row['status'] = 'interview'
        if old == 'closed':
            row['status'] = 'closed'
        elif old == 'interview' and row.get('status') not in ('closed', 'uncertain'):
            row['status'] = 'interview'
        row.setdefault('status', 'pending')
    # An unresolved attempt always wins over a later observation or screen.
    for a in state['attempts']:
        key = ('boss', a['target']['account'], a['target']['threadKey'])
        if a['status'] in ('prepared', 'uncertain') and key in rows:
            rows[key]['status'] = 'uncertain'
    records = sorted(rows.values(), key=lambda r: r['history'][-1].get('ts', 0), reverse=True)
    counts = {k: sum(r['status'] == k for r in records) for k in ('qualified', 'pending', 'excluded', 'waiting', 'replied', 'interview', 'closed', 'uncertain')}
    return {**state, 'workspace': str(root), 'configured': bool(state['profile']), 'records': records,
            'counts': {**counts, 'total': len(records), 'decisions': sum(d['status'] == 'open' for d in state['decisions'])}}


def guard(state, run, attempt=None):
    require(not state['paused'], 'PAUSED', '已暂停，停止页面操作')
    require(now() - run['startedTs'] < 3600, 'RUN_EXPIRED', '本轮已超过一小时，请收尾后重新开始')
    require(not run['noSend'], 'NO_SEND', '本轮只看，不允许外发')
    unresolved = [a for a in state['attempts'] if a['status'] in ('prepared', 'uncertain') and (attempt is None or a['id'] != attempt['id'])]
    require(not unresolved, 'UNRESOLVED_SEND', '存在未核对的外发，先核对页面结果')
    if attempt:
        require(attempt['status'] == 'prepared' and attempt['runId'] == run['id'], 'ATTEMPT_CLOSED', '本次尝试已结束，不能再次发送')
        require(now() - attempt['ts'] <= 600, 'ATTEMPT_EXPIRED', '准备已超过十分钟，停止并核对是否发送')
        require(attempt['kind'] in run['actions'], 'NOT_AUTHORIZED', '本轮未授权此动作')
        if attempt['decisionId']:
            d = next(d for d in state['decisions'] if d['id'] == attempt['decisionId'])
            require(d['status'] == 'answered' and d.get('approvedText') == attempt['text'] and d.get('revision') == attempt.get('decisionRevision'), 'DECISION_CHANGED', '决定已改变')
    return {'allowed': True}


def stage(state, req, root):
    require(not state['run'], 'RUN_ACTIVE', '任务运行中，先收尾再更新简历')
    source = Path(text(req.get('source'), '用户提供的简历路径')).expanduser().resolve()
    require(source.is_file() and source.stat().st_size <= MAX_RESUME, 'INVALID_RESUME', '简历不存在或超过 5 MB')
    suffix = source.suffix.lower()
    require(suffix in ('.pdf', '.docx', '.html', '.htm', '.txt', '.md', '.rtf'), 'INVALID_RESUME', '请提供 PDF、DOCX、HTML、TXT、Markdown 或 RTF')
    data = source.read_bytes()
    require(0 < len(data) <= MAX_RESUME, 'INVALID_RESUME', '简历为空或过大')
    digest = hashlib.sha256(data).hexdigest()
    dest = root / 'resumes' / (digest + suffix)
    require(not dest.parent.is_symlink() and not dest.is_symlink(), 'UNSAFE_PATH', '简历目录包含符号链接')
    dest.parent.mkdir(mode=0o700, exist_ok=True)
    if dest.exists():
        require(hashlib.sha256(dest.read_bytes()).hexdigest() == digest, 'RESUME_CHANGED', '已存简历损坏')
    else:
        with dest.open('xb') as out:
            out.write(data)
        os.chmod(dest, 0o600)
    resume = {'name': source.name, 'path': str(dest), 'sha256': digest, 'at': stamp(),
              'extractedText': text(req.get('extractedText', ''), '提取文本', required=False)}
    state['resume'] = resume
    # New facts need review, but old facts and authorization are retained until configure.
    state['resumeNeedsReview'] = True
    return resume


def dispatch(state, req, root):
    op = req['op']
    if op == 'stage_resume':
        return stage(state, req, root)
    if op == 'configure':
        expected(state, req)
        require(not state['run'], 'RUN_ACTIVE', '先结束当前任务，再修改资料')
        require(state['resume'], 'RESUME_REQUIRED', '请先提供一份简历')
        state['profile'] = profile(req.get('profile'))
        state['resumeNeedsReview'] = False
        return {'configured': True}
    if op == 'begin':
        expected(state, req)
        read_only_followup = req.get('skill') == 'followup' and req.get('noSend') is True
        require(read_only_followup or (state['profile'] and not state.get('resumeNeedsReview')), 'ONBOARDING_REQUIRED', '先读取简历并确认求职偏好')
        require(not state['run'], 'RUN_ACTIVE', '已有任务，请核对旧任务是否结束；不自动接管')
        require(not state['paused'], 'PAUSED', '工作区已暂停')
        skill = req.get('skill')
        require(skill in ('apply', 'followup'), 'INVALID_INPUT', 'skill 必须是 apply/followup')
        account = text(req.get('account'), '页面账号', limit=1000)
        text(req.get('accountEvidence'), '页面账号核对依据', limit=2000)
        require(state['account'] in (None, account), 'ACCOUNT_MISMATCH', '账号与工作区不同，请使用另一个工作区')
        actions = strings(req.get('actions', []), '本轮发送范围')
        require(set(actions) <= (ACTIONS - {'greet'} if skill == 'followup' else {'greet'}), 'INVALID_INPUT', '动作超出当前技能')
        reason = text(req.get('authorizationEvidence', ''), '本人授权依据', required=False, limit=10000)
        require(not (set(actions) - set((state['profile'] or {}).get('authorization', []))) or reason, 'NOT_AUTHORIZED', '缺少本轮明确发送授权')
        no_send = boolean(req.get('noSend'), 'noSend')
        maximum = integer(req.get('maxActions', 10), 0, 20, '单轮上限')
        targets = strings(req.get('targets', []), '指定会话范围')
        run = {'id': uid('run'), 'skill': skill, 'account': account, 'startedTs': now(), 'at': stamp(),
               'actions': [] if no_send else actions, 'noSend': no_send, 'maxActions': maximum,
               'targets': targets, 'authorizationEvidence': reason, 'accountEvidence': req['accountEvidence']}
        state['account'] = account
        state['run'] = run
        return run
    if op == 'pause':
        state['paused'] = boolean(req.get('paused'), 'paused')
        return {'paused': state['paused']}
    if op == 'recover_run':
        text(req.get('evidence'), '已确认旧执行器停止的依据', limit=10000)
        run = active(state, req)
        for a in state['attempts']:
            if a['runId'] == run['id'] and a['status'] == 'prepared':
                a['status'] = 'uncertain'
                event(state, a['kind'], a['target'], status='uncertain', text=a['text'], attemptId=a['id'])
                decision(state, a['target'], '请核对这次发送是否实际送达；结果不明时不能重发。', a['id'])
        state['run'] = None
        return {'recovered': True}
    if op == 'answer':
        expected(state, req)
        d = next((d for d in state['decisions'] if d['id'] == req.get('decisionId')), None)
        require(d and d['status'] in ('open', 'answered'), 'DECISION_CLOSED', '事项不存在或已经发送/处理')
        if d['status'] == 'answered':
            d.setdefault('previousAnswers', []).append({k: d.get(k) for k in ('answer', 'approvedText', 'revision')})
        d['revision'] = d.get('revision', 0) + 1
        d['answer'] = text(req.get('answer'), '本人回答')
        d['approvedText'] = text(req.get('approvedText', ''), '批准的外发原文', required=False)
        d['status'] = 'answered'
        return d
    if op == 'reconcile':
        require(not state['run'], 'RUN_ACTIVE', '先结束旧任务，再核对发送结果')
        a = next((a for a in state['attempts'] if a['id'] == req.get('attemptId')), None)
        require(a and a['status'] in ('uncertain', 'prepared'), 'ATTEMPT_CLOSED', '没有待核对的尝试')
        status = req.get('status')
        require(status in ('sent', 'failed_no_send'), 'INVALID_INPUT', '只有明确送达或明确未发才能解除不明状态')
        evidence = text(req.get('evidence'), '页面核对证据')
        a.update(status=status, evidence=evidence, resolvedAt=stamp())
        actual = text(req.get('actualText', a.get('actualText', a['text'])), '实际发送文字')
        require(a['kind'] != 'resume' or actual == a['text'], 'RESUME_MISMATCH', '简历内容不能替换')
        a['actualText'] = actual
        event(state, a['kind'], a['target'], status=status, text=actual, plannedText=a['text'], evidence=evidence, attemptId=a['id'])
        if status == 'sent' and a['decisionId']:
            next(d for d in state['decisions'] if d['id'] == a['decisionId'])['status'] = 'resolved'
        for d in state['decisions']:
            if d.get('key') == a['id']:
                d['status'] = 'resolved'
        return a
    run = active(state, req)
    if op == 'end':
        for a in state['attempts']:
            if a['runId'] == run['id'] and a['status'] == 'prepared':
                a['status'] = 'uncertain'
                event(state, a['kind'], a['target'], status='uncertain', text=a['text'], attemptId=a['id'])
                decision(state, a['target'], '任务结束时仍有未核对发送，请先核对真实会话。', a['id'])
        state['run'] = None
        return {'ended': True}
    if op == 'record':
        target = identity(req.get('target'), run['account'])
        require(not run['targets'] or target['threadKey'] in run['targets'], 'OUT_OF_SCOPE', '目标不在本轮范围')
        kind = req.get('kind')
        require(kind in ('screen', 'inbound', 'observe'), 'INVALID_EVENT', '外发必须经过 prepare/guard/finish')
        fields = {'text': text(req.get('text', ''), '内容摘要', required=False), 'note': text(req.get('note', ''), '备注', required=False), 'runId': run['id']}
        if kind == 'inbound':
            key = text(req.get('messageKey'), '页面原始消息标识（可见 ID 或时间与原文的稳定组合）', limit=2000)
            previous = next((e for e in state['events'] if e['kind'] == 'inbound' and same(e, target) and e.get('messageKey') == key), None)
            if previous:
                return previous
            fields['messageKey'] = key
        if kind == 'screen':
            require(req.get('result') in ('qualified', 'pending', 'excluded'), 'INVALID_EVENT', '筛选结论无效')
            fields.update(result=req['result'], reasons=strings(req.get('reasons'), '筛选依据', True), facts=req.get('facts', {}))
            require(isinstance(fields['facts'], dict), 'INVALID_EVENT', '职位事实需要对象')
        if kind == 'observe':
            require(req.get('observed') in OBSERVATIONS, 'INVALID_EVENT', '观察类型无效')
            fields['observed'] = req['observed']
        result = event(state, kind, target, **fields)
        if kind == 'observe' and req['observed'] == 'interview_invited':
            decision(state, target, '收到面试邀约，请本人决定时间及回复。', 'interview:' + target['threadKey'])
        return result
    if op == 'decide':
        target = identity(req.get('target'), run['account'])
        require(not run['targets'] or target['threadKey'] in run['targets'], 'OUT_OF_SCOPE', '目标不在本轮范围')
        return decision(state, target, text(req.get('question'), '待决定问题'), req.get('key'))
    if op == 'prepare':
        guard(state, run)
        target = identity(req.get('target'), run['account'])
        require(not run['targets'] or target['threadKey'] in run['targets'], 'OUT_OF_SCOPE', '目标不在本轮范围')
        kind = req.get('kind')
        require(kind in run['actions'], 'NOT_AUTHORIZED', '本轮未授权此动作')
        require(not run['targets'] or target['threadKey'] in run['targets'], 'OUT_OF_SCOPE', '目标不在本轮授权范围')
        content = text(req.get('text'), '最终外发内容')
        attempts = state['attempts']
        require(sum(a['runId'] == run['id'] for a in attempts) < run['maxActions'], 'RUN_LIMIT', '已达到本轮外发上限')
        recent = [a for a in attempts if now() - a['ts'] < 86400]
        require(len(recent) < 20, 'DAILY_LIMIT', '已达到过去 24 小时的 20 次外发上限')
        require(not recent or now() - max(a.get('finishedTs', a['ts']) for a in recent) >= 60, 'WAIT', '两次外发至少间隔 60 秒')
        history = [e for e in state['events'] if same(e, target)]
        require(not any(e['kind'] == 'observe' and e.get('observed') in TERMINAL for e in history), 'THREAD_CLOSED', '会话已结束或被拒绝')
        if kind == 'greet':
            max_chars = integer(req.get('maxChars', 100), 1, 10000, '招呼字数上限')
            require(len(content) <= max_chars, 'GREETING_TOO_LONG', '招呼超过字数上限，请精简草稿或核对本人指定的上限')
            screens = [e for e in history if e['kind'] == 'screen' and e.get('runId') == run['id']]
            require(screens and screens[-1]['result'] == 'qualified', 'SCREEN_REQUIRED', '本轮需先确认职位合格并记录依据')
            already_contacted = any(contacted(state, e) and e.get('account') == target['account'] and e.get('company', '').strip().casefold() == target['company'].strip().casefold() for e in state['events'])
            require(not already_contacted, 'ALREADY_CONTACTED', '同公司已有联系记录，先核对既有会话')
        else:
            require(any(contacted(state, e) for e in history), 'HISTORY_REQUIRED', '先读取并记录既有会话')
        if kind == 'nudge':
            last_inbound = max((e.get('ts', 0) for e in history if e['kind'] == 'inbound'), default=0)
            nudges = [a for a in attempts if same(a['target'], target) and a['kind'] == 'nudge' and a['ts'] > last_inbound]
            require(len(nudges) < 2, 'NUDGE_LIMIT', '最多两次未回复跟进')
            last = max((e.get('ts', 0) for e in history if e['kind'] in ACTIONS | {'inbound'}), default=now())
            require(last > 0, 'NUDGE_TIME_UNKNOWN', '无法确认上次沟通时间，先核对页面')
            require(now() - last >= 3 * 86400, 'NUDGE_WAIT', '距离上次沟通不足三天')
        if kind == 'resume':
            require(state['resume'] and not state.get('resumeNeedsReview'), 'RESUME_REQUIRED', '需要核对当前简历')
            text(req.get('requestEvidence'), '对方索要简历的依据')
            require(content == state['resume']['sha256'], 'RESUME_MISMATCH', '简历内容需绑定已确认原件的 SHA-256')
        did = req.get('decisionId')
        if req.get('commitment'):
            require(did, 'DECISION_REQUIRED', '承诺事项需要本人批准原话')
        if did:
            d = next((d for d in state['decisions'] if d['id'] == did), None)
            require(d and d['status'] == 'answered' and same(d['target'], target) and d.get('approvedText') == content, 'DECISION_REQUIRED', '需要本人对该目标批准的逐字原文')
        a = {'id': uid('attempt'), 'ts': now(), 'at': stamp(), 'runId': run['id'], 'target': target,
             'kind': kind, 'text': content, 'status': 'prepared', 'decisionId': did, 'decisionRevision': d.get('revision') if did else None, 'guarded': False}
        if kind == 'greet':
            a['maxChars'] = max_chars
        attempts.append(a)
        return a
    if op in ('guard', 'finish'):
        a = next((a for a in state['attempts'] if a['id'] == req.get('attemptId')), None)
        require(a and a['runId'] == run['id'], 'ATTEMPT_MISMATCH', '外发尝试不属于本轮')
        if op == 'guard':
            require(identity(req.get('target'), run['account']) == a['target'], 'TARGET_CHANGED', '当前页面目标改变')
            require(req.get('text') == a['text'], 'CONTENT_CHANGED', '最终内容改变，不能发送')
            result = guard(state, run, a)
            a['guarded'] = True
            return result
        require(a['status'] == 'prepared', 'ATTEMPT_CLOSED', '已记录结果，不能重复完成')
        require(req.get('status') in RESULTS, 'INVALID_INPUT', '发送结果无效')
        require(req['status'] != 'sent' or a['guarded'], 'GUARD_REQUIRED', '没有发送前核对，不能记录送达')
        evidence = text(req.get('evidence'), '页面结果依据')
        actual = text(req.get('actualText', a['text']), '实际发送文字')
        require(a['kind'] != 'resume' or actual == a['text'], 'RESUME_MISMATCH', '简历内容不能替换')
        a.update(status=req['status'], evidence=evidence, actualText=actual, finishedTs=now())
        event(state, a['kind'], a['target'], status=a['status'], text=actual, plannedText=a['text'], evidence=evidence, attemptId=a['id'])
        if a['status'] == 'uncertain':
            decision(state, a['target'], '发送结果不明，请核对真实会话；不能重发。', a['id'])
        elif a['status'] == 'sent' and a['decisionId']:
            next(d for d in state['decisions'] if d['id'] == a['decisionId'])['status'] = 'resolved'
        return a
    raise Problem('UNKNOWN_OP', '未知操作')


OPS = {'snapshot', 'stage_resume', 'configure', 'begin', 'pause', 'recover_run', 'answer', 'reconcile', 'end', 'record', 'decide', 'prepare', 'guard', 'finish'}


def execute(root, req):
    require(isinstance(req, dict) and req.get('op') in OPS, 'UNKNOWN_OP', '需要合法 op')
    root = Path(root).expanduser().absolute()
    require(root != Path(root.anchor) and root.resolve() != Path.home().resolve(), 'UNSAFE_PATH', '不能在系统根目录或用户主目录根部创建工作区')
    dbfile = root / 'job-mate.sqlite3'
    require(not root.is_symlink() and not dbfile.is_symlink(), 'UNSAFE_PATH', '工作区或数据库不能是符号链接')
    if not dbfile.exists():
        legacy = [name for name in ('job-mate.json', 'profile.json', 'ledger.jsonl') if (root / name).exists()]
        require(not legacy, 'LEGACY_WORKSPACE', '检测到不支持的工作区格式，已保留原文件；请停止任务并核对数据来源，不要覆盖或换目录绕开历史记录')
        if req['op'] == 'snapshot':
            return summary(fresh(), root)
        require(req['op'] == 'stage_resume', 'ONBOARDING_REQUIRED', '请先提供简历')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(str(dbfile), timeout=5, isolation_level=None)
    try:
        connection.execute('PRAGMA busy_timeout=5000')
        connection.execute('BEGIN IMMEDIATE' if req['op'] != 'snapshot' else 'BEGIN')
        exists = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace'").fetchone()
        if not exists:
            require(req['op'] == 'stage_resume', 'INVALID_WORKSPACE', '工作区结构不完整，请保留文件并恢复')
            connection.execute('CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            state = fresh()
            connection.execute('INSERT INTO workspace VALUES (1, ?)', (json.dumps(state),))
        else:
            row = connection.execute('SELECT data FROM workspace WHERE id=1').fetchone()
            require(row, 'INVALID_WORKSPACE', '缺少工作区数据')
            state = json.loads(row[0])
        require(state.get('schemaVersion') == SCHEMA, 'VERSION_MISMATCH', '工作区版本不兼容，请更新技能')
        if req['op'] == 'snapshot':
            result = summary(state, root)
        else:
            result = dispatch(state, req, root)
            state['revision'] += 1
            state['updatedAt'] = stamp()
            connection.execute('UPDATE workspace SET data=? WHERE id=1', (json.dumps(state, ensure_ascii=False, allow_nan=False),))
            result = {'result': result, 'revision': state['revision']}
        connection.commit()
        if req['op'] != 'snapshot':
            os.chmod(dbfile, 0o600)
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def resolve_workspace(workspace=None, project_dir=None):
    explicit = workspace or os.environ.get('JOB_MATE_HOME')
    if explicit:
        return Path(explicit).expanduser().absolute()
    if project_dir:
        project = Path(project_dir).expanduser()
        require(project.is_absolute() and project.is_dir(), 'INVALID_PROJECT', '项目目录必须是已存在的绝对路径')
        return project / 'job-mate'
    return Path.home() / '.job-mate'


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', help='Explicit data workspace; overrides JOB_MATE_HOME and project default')
    parser.add_argument('--project-dir', help='Chat project root (absolute path); defaults data to <project>/job-mate')
    parser.add_argument('--request', help='JSON request file; otherwise read stdin')
    args = parser.parse_args()
    try:
        raw = Path(args.request).read_text(encoding='utf-8') if args.request else sys.stdin.read(2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, 'REQUEST_TOO_LARGE', '请求过大')
        req = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))
        result = {'ok': True, **execute(resolve_workspace(args.workspace, args.project_dir), req)}
    except Problem as e:
        result = {'ok': False, 'error': {'code': e.code, 'message': str(e)}}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as e:
        result = {'ok': False, 'error': {'code': 'WORKSPACE_ERROR', 'message': '无法读取或修改工作区，请保留原文件并检查权限、格式与版本（' + type(e).__name__ + '）'}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
