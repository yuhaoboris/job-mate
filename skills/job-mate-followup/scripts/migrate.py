#!/usr/bin/env python3
"""Copy a known v1 workspace to a new paused v2 workspace; never modify the source."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from job_mate import ACTIONS, OBSERVATIONS, Problem, decision, fresh, require, stamp, uid


def migrate(source, target):
    source, target = Path(source).expanduser().absolute(), Path(target).expanduser().absolute()
    require(source.is_dir() and not source.is_symlink(), 'SOURCE_INVALID', '旧工作区不存在或是符号链接')
    require(not target.exists() and source.resolve() not in target.resolve().parents, 'TARGET_EXISTS', '目标必须是旧目录以外的全新目录')
    require((source / 'job-mate.json').is_file() and (source / 'profile.json').is_file(), 'SOURCE_INVALID', '需要 1.0 marker 和 profile')
    for p in source.rglob('*'):
        require(not p.is_symlink() and (p.is_dir() or p.is_file()), 'UNSAFE_PATH', '旧目录有链接或特殊文件，需先人工核对')
    marker = json.loads((source / 'job-mate.json').read_text(encoding='utf-8'))
    require(marker.get('schemaVersion') == 1, 'VERSION_MISMATCH', '只支持 schemaVersion 1')
    state = fresh()
    state['paused'] = True
    state['legacyReviewRequired'] = True
    ledger = source / 'ledger/events.jsonl'
    require(ledger.exists(), 'SOURCE_INVALID', '旧工作区缺少 ledger/events.jsonl，不能当作空历史')
    seen, attempts = set(), {}
    for number, line in enumerate(ledger.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        require(isinstance(raw, dict), 'INVALID_EVENT', '旧台账第 %s 行不是对象' % number)
        kind = raw.get('kind')
        require(kind in ACTIONS | {'intro', 'screen', 'inbound', 'observe'}, 'INVALID_EVENT', '旧台账第 %s 行有未知事件' % number)
        if raw.get('eventId') and raw['eventId'] in seen:
            continue
        seen.add(raw.get('eventId'))
        account = raw.get('account')
        company, title = raw.get('company'), raw.get('title')
        # Global observations are retained in legacy, and surface as a review decision.
        if not all(isinstance(x, str) and x for x in (account, company, title)):
            require(kind == 'observe', 'INVALID_EVENT', '旧台账第 %s 行身份缺失' % number)
            continue
        thread = raw.get('threadKey') or json.dumps([company, title, raw.get('recruiter')], ensure_ascii=False)
        target_row = {'platform': 'boss', 'account': account, 'company': company, 'title': title, 'threadKey': thread}
        require(raw.get('platform', 'boss') == 'boss', 'UNSUPPORTED_PLATFORM', '旧数据包含非 BOSS 记录')
        ts = 0
        if raw.get('at'):
            try:
                ts = datetime.datetime.fromisoformat(raw['at'].replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError):
                pass
        row = {**raw, **target_row, 'id': raw.get('eventId') or uid('legacy'), 'ts': ts, 'legacy': True}
        if kind == 'intro':
            row['kind'] = 'reply'
        if kind == 'screen':
            require(raw.get('result') in ('qualified', 'pending', 'excluded'), 'INVALID_EVENT', '旧筛选结论无效')
        if kind == 'observe':
            if raw.get('observed') == 'risk':
                row['observed'] = 'rejected'
            elif raw.get('observed') not in OBSERVATIONS:
                row['legacyObserved'] = raw.get('observed')
                row['observed'] = 'info'
        if row['kind'] in ACTIONS:
            status = raw.get('status')
            if status == 'skipped':
                row['status'] = 'failed_no_send'
            elif status == 'already_contacted':
                row['kind'] = 'inbound'
                row['text'] = '旧记录表明已有联系；送达内容未知。'
            else:
                require(status in ('sent', 'failed_no_send', 'pending', 'uncertain', None), 'INVALID_EVENT', '旧外发结果无效')
                row['status'] = status if status in ('sent', 'failed_no_send') else 'uncertain'
                aid = raw.get('attemptId') or uid('legacy-attempt')
                attempts[aid] = {'id': aid, 'ts': ts, 'at': row.get('at'), 'runId': 'legacy', 'target': target_row,
                                 'kind': row['kind'], 'text': row.get('text') or '', 'status': row['status'], 'decisionId': None, 'guarded': False}
        state['events'].append(row)
    state['attempts'] = list(attempts.values())
    for a in state['attempts']:
        if a['status'] == 'uncertain':
            decision(state, a['target'], '旧版外发结果不明，请核对真实会话。', a['id'])
    decision(state, {}, '请核对 legacy/ 中的全部个人事实、偏好、停止名单、简历、账号和旧锁，再确认新资料及解除暂停。', 'legacy-review')
    accounts = {e['account'] for e in state['events']}
    if len(accounts) == 1:
        state['account'] = next(iter(accounts))
    require(len(accounts) <= 1, 'MULTIPLE_ACCOUNTS', '旧工作区有多个账号，需先分别迁移，不能合并身份')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.job-mate-migrate-', dir=target.parent))
    try:
        shutil.copytree(source, staging / 'legacy')
        db = staging / 'job-mate.sqlite3'
        connection = sqlite3.connect(str(db))
        try:
            connection.execute('CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            connection.execute('INSERT INTO workspace VALUES(1,?)', (json.dumps(state, ensure_ascii=False),))
            connection.commit()
        finally:
            connection.close()
        os.chmod(db, 0o600)
        require(not target.exists(), 'TARGET_EXISTS', '目标已被创建，未覆盖')
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'workspace': str(target), 'events': len(state['events']), 'paused': True, 'configured': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps({'ok': True, **migrate(args.source, args.target)}, ensure_ascii=False))
    except (Problem, OSError, ValueError) as e:
        print(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))
        raise SystemExit(2)
