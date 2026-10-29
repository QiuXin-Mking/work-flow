"""Persistent, version-bound gates for repair planning."""
import hashlib
import json
import time
import re
from typing import Any

def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def initial(snapshot: dict[str, Any], version: int=1) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for kind, field in [('mobi', 'mobi'), ('trace', 'traceId')]:
        values = ticket_identifiers(snapshot, kind)
        if len(values) == 1:
            inputs[field] = next(iter(values))
    return dict(stage='information', revision=1, snapshot_version=version, source_snapshot_version=version, snapshot_hash=digest(snapshot), input=inputs, information=None, root_cause=None, business_confirmation=None, prd=None, prd_reviews=[], ready_for_implementation=False)

def ticket_identifiers(snapshot: dict[str, Any], kind: str) -> set[str]:
    pattern = '(?i)\\bmobi\\s*[:：]\\s*([a-z0-9_-]+)' if kind == 'mobi' else '(?i)\\btrace[-_ ]?id\\s*[:：=]\\s*([a-z0-9_-]+)'
    values: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            values.update(re.findall(pattern, value))
        elif isinstance(value, dict):
            for key, child in value.items():
                if key != 'supplement':
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(snapshot)
    return values

def validate_inputs(inputs: Any) -> None:
    if not isinstance(inputs, dict):
        raise ValueError('信息必须是对象')
    for key in ('mobi', 'environment', 'reproduction', 'steps', 'expected', 'actual', 'traceId', 'occurredAt', 'notes'):
        if key in inputs and (not isinstance(inputs[key], str)):
            raise ValueError('信息字段必须是字符串：' + key)
    if 'triggeredOn74' in inputs and type(inputs['triggeredOn74']) is not bool:
        raise ValueError('triggeredOn74 必须是布尔值')

def validate_report(stage: str, report: Any) -> None:
    if not isinstance(report, dict):
        raise ValueError('阶段报告必须是对象')
    flag = {'information': 'complete', 'root_cause': 'confirmed', 'prd': 'converged', 'prd_review': 'converged'}.get(stage)
    if not flag or type(report.get(flag)) is not bool:
        raise ValueError('阶段判定必须是布尔值')
    for key in ('missing', 'questions'):
        if key in report and (not isinstance(report[key], list) or any((not isinstance(v, str) for v in report[key]))):
            raise ValueError('缺失项与问题必须是字符串数组')
    if report[flag] and any(report.get(key, []) for key in ('missing', 'questions')):
        raise ValueError('仍有缺失项或未决问题，不能判定通过')
    if stage == 'root_cause' and report['confirmed']:
        if not isinstance(report.get('root_cause'), str) or not report['root_cause'].strip():
            raise ValueError('根因结论不能为空')
        changes = report.get('business_changes')
        if not isinstance(changes, list) or not 1 <= len(changes) <= 3 or any((not isinstance(x, dict) or not isinstance(x.get('id'), str) or (not x['id'].strip()) or (not isinstance(x.get('description'), str)) or (not x['description'].strip()) for x in changes)):
            raise ValueError('业务变更格式无效')
        evidence = report.get('evidence')
        if not isinstance(evidence, dict):
            raise ValueError('证据格式无效')
        for key in ('logs', 'code'):
            v = evidence.get(key)
            if not (isinstance(v, str) and v.strip() or (isinstance(v, list) and v and all((isinstance(item, (str, dict)) and bool(item) for item in v)))):
                raise ValueError('日志与代码证据不能为空')
    if stage == 'prd_review' and (not isinstance(report.get('prd_hash'), str) or not report['prd_hash']):
        raise ValueError('必须指定审查的 PRD 哈希')

class WorkflowStore:

    def _workflow_schema(self, db: Any) -> None:
        db.executescript('CREATE TABLE IF NOT EXISTS job_workflows(job_id TEXT PRIMARY KEY, data_json TEXT NOT NULL);\n        CREATE TABLE IF NOT EXISTS job_snapshots(job_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(job_id,version));')

    def _load_workflow(self, db: Any, job_id: str) -> dict[str, Any] | None:
        row = db.execute('SELECT data_json FROM job_workflows WHERE job_id=?', (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write_workflow(self, db: Any, job_id: str, data: dict[str, Any]) -> None:
        db.execute('INSERT INTO job_workflows VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET data_json=excluded.data_json', (job_id, json.dumps(data, ensure_ascii=False)))

    def get_workflow(self, job_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            return self._load_workflow(db, job_id)

    def ensure_workflow(self, job_id: str) -> dict[str, Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT snapshot_json FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            data = self._load_workflow(db, job_id)
            if data is None:
                snap = json.loads(row[0] or '{}')
                data = initial(snap)
                db.execute('INSERT OR IGNORE INTO job_snapshots VALUES(?,?,?,?)', (job_id, 1, json.dumps(snap), time.time()))
                self._write_workflow(db, job_id, data)
            return data

    def get_snapshot_version(self, job_id: str, version: int) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute('SELECT snapshot_json FROM job_snapshots WHERE job_id=? AND version=?', (job_id, version)).fetchone()
            return json.loads(row[0]) if row else None

    def _owner_workflow(self, db: Any, job_id: str, user_id: str, revision: int) -> tuple[Any, dict[str, Any]]:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row['created_by'] != user_id or not db.execute('SELECT 1 FROM users WHERE id=? AND active=1', (user_id,)).fetchone():
            raise PermissionError('only active creator may change workflow')
        w = self._load_workflow(db, job_id)
        if w is None or w['revision'] != revision:
            raise ValueError('工作流版本已变化，请刷新')
        return (row, w)

    def _update_gate(self, db: Any, job_id: str, w: dict[str, Any], status: str, message: str) -> dict[str, Any]:
        w['revision'] += 1
        self._write_workflow(db, job_id, w)
        now = time.time()
        db.execute('UPDATE jobs SET status=?,error=NULL,candidate_id=NULL,lease_epoch=lease_epoch+1,lease_owner=NULL,lease_expires=NULL,updated_at=? WHERE id=?', (status, now, job_id))
        db.execute("UPDATE attempts SET status='superseded' WHERE job_id=? AND status='running'", (job_id,))
        db.execute('INSERT INTO events(job_id,kind,payload_json,created_at) VALUES(?,?,?,?)', (job_id, 'workflow', json.dumps({'message': message, 'stage': w['stage'], 'revision': w['revision']}, ensure_ascii=False), now))
        return w

    def submit_workflow_information(self, job_id: str, user_id: str, revision: int, inputs: dict[str, Any]) -> dict[str, Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, w = self._owner_workflow(db, job_id, user_id, revision)
            if row['status'] in ('delivered', 'review_ready', 'delivery_wip', 'wip_incomplete'):
                raise ValueError('此状态不能修改输入')
            validate_inputs(inputs)
            version = w['snapshot_version'] + 1
            source = json.loads(row['snapshot_json'] or '{}')
            snap = {**source, 'supplement': inputs}
            db.execute('INSERT INTO job_snapshots VALUES(?,?,?,?)', (job_id, version, json.dumps(snap, ensure_ascii=False), time.time()))
            new = initial(snap, version)
            new['revision'] = w['revision']
            new['input'] = inputs
            new['source_snapshot_version'] = w.get('source_snapshot_version', 1)
            return self._update_gate(db, job_id, new, 'queued', '信息已补充，重新诊断')

    def refresh_workflow_snapshot(self, job_id: str, user_id: str, revision: int, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, workflow = self._owner_workflow(db, job_id, user_id, revision)
            if row['status'] in ('delivered', 'review_ready', 'delivery_wip', 'wip_incomplete'):
                raise ValueError('此状态不能刷新工单')
            if not isinstance(snapshot, dict) or not snapshot:
                raise ValueError('工单快照不能为空')
            version = workflow['snapshot_version'] + 1
            db.execute('INSERT INTO job_snapshots VALUES(?,?,?,?)', (job_id, version, json.dumps(snapshot, ensure_ascii=False), time.time()))
            db.execute('UPDATE jobs SET snapshot_json=? WHERE id=?', (json.dumps(snapshot, ensure_ascii=False), job_id))
            fresh = initial(snapshot, version)
            fresh['revision'] = workflow['revision']
            fresh['input'] = {**workflow['input'], **fresh['input']}
            return self._update_gate(db, job_id, fresh, 'queued', '工单已刷新，旧结论已失效')

    def confirm_business(self, job_id: str, user_id: str, revision: int, choice: str, selected_change_ids: list[str], comment: str='') -> dict[str, Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, w = self._owner_workflow(db, job_id, user_id, revision)
            if row['status'] != 'awaiting_business_confirmation' or w['stage'] != 'business_confirmation':
                raise ValueError('当前不等待业务确认')
            if choice not in ('confirm', 'modify', 'reject'):
                raise ValueError('invalid choice')
            ids = [x['id'] for x in w['root_cause']['business_changes']]
            if choice == 'confirm' and (not selected_change_ids or len(set(selected_change_ids)) != len(selected_change_ids) or (not set(selected_change_ids) <= set(ids))):
                raise ValueError('请选择有效业务变更')
            if choice == 'modify' and (not comment.strip()):
                raise ValueError('请说明需要修改的业务范围')
            w['business_confirmation'] = dict(choice=choice, selectedChangeIds=selected_change_ids, comment=comment, user_id=user_id, root_cause_hash=digest(w['root_cause']))
            w['stage'] = 'prd' if choice == 'confirm' else 'root_cause'
            w['prd'] = None
            w['prd_reviews'] = []
            w['ready_for_implementation'] = False
            return self._update_gate(db, job_id, w, 'queued' if choice != 'reject' else 'cancelled', '业务范围已确认' if choice == 'confirm' else '业务范围需要调整' if choice == 'modify' else '使用者拒绝业务变更')

    def submit_prd_feedback(self, job_id: str, user_id: str, revision: int, answer: str) -> dict[str, Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, w = self._owner_workflow(db, job_id, user_id, revision)
            if row['status'] != 'awaiting_prd_input' or w['stage'] not in ('prd', 'prd_review'):
                raise ValueError('当前不等待 PRD 答复')
            if not answer.strip():
                raise ValueError('请填写答复')
            w.setdefault('prd_feedback', []).append(dict(answer=answer, user_id=user_id, revision=revision))
            w['stage'] = 'prd'
            w['prd_reviews'] = []
            w['ready_for_implementation'] = False
            return self._update_gate(db, job_id, w, 'queued', 'PRD 问题已答复')

    def workflow_implementation_ready(self, job_id: str) -> bool:
        with self._db() as db:
            row = db.execute('SELECT snapshot_json FROM jobs WHERE id=?', (job_id,)).fetchone()
            workflow = self._load_workflow(db, job_id)
            if not row or not workflow or workflow['stage'] != 'implementation' or workflow.get('ready_for_implementation') is not True:
                return False
            source = json.loads(row['snapshot_json'] or '{}')
            if workflow['snapshot_version'] != workflow.get('source_snapshot_version', 1):
                source = {**source, 'supplement': workflow['input']}
            return digest(source) == workflow['snapshot_hash']

    def record_workflow_result(self, job_id: str, epoch: int, stage: str, report: dict[str, Any]) -> bool:
        validate_report(stage, report)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            w = self._load_workflow(db, job_id)
            if not row or not w or row['status'] != 'running' or (row['lease_epoch'] != epoch) or (w['stage'] != stage):
                return False
            source = json.loads(row['snapshot_json'] or '{}')
            if w['snapshot_version'] != w.get('source_snapshot_version', 1):
                source = {**source, 'supplement': w['input']}
            if digest(source) != w['snapshot_hash']:
                return False
            status = 'queued'
            if stage == 'information':
                if report.get('complete') is True:
                    missing = [k for k in ('mobi', 'environment', 'steps', 'expected', 'actual') if not isinstance(w['input'].get(k), str) or not w['input'][k].strip()]
                    original = json.loads(row['snapshot_json'] or '{}')
                    if w['input'].get('mobi') not in ticket_identifiers(original, 'mobi'):
                        raise ValueError('mobi 必须与工单中的 mobi: 标记一致')
                    if missing:
                        raise ValueError('必要信息不完整：' + ','.join(missing))
                    mode = w['input'].get('reproduction')
                    if mode not in ('always', 'intermittent'):
                        raise ValueError('请确认复现类型')
                    if mode == 'intermittent':
                        snap = db.execute('SELECT snapshot_json FROM job_snapshots WHERE job_id=? AND version=?', (job_id, w.get('source_snapshot_version', 1))).fetchone()
                        text = snap[0] if snap else ''
                        trace = w['input'].get('traceId', '')
                        if w['input'].get('triggeredOn74') is not True or not trace or trace not in ticket_identifiers(json.loads(text or '{}'), 'trace'):
                            raise ValueError('偶现问题必须在74触发且工单含对应trace-id')
                    w['stage'] = 'root_cause'
                else:
                    status = 'needs_input'
                w['information'] = report
            elif stage == 'root_cause':
                w['root_cause'] = report
                if report.get('confirmed') is True:
                    ev = report.get('evidence', {})
                    changes = report.get('business_changes', [])
                    if not isinstance(ev, dict) or not ev.get('logs') or (not ev.get('code')) or (not 1 <= len(changes) <= 3) or any((not x.get('id') or not x.get('description') for x in changes)) or (len({x['id'] for x in changes}) != len(changes)):
                        raise ValueError('根因证据或业务变更不完整')
                    w['stage'] = 'business_confirmation'
                    status = 'awaiting_business_confirmation'
                else:
                    status = 'needs_input'
            elif stage == 'prd':
                if not w['root_cause'] or not w['root_cause'].get('confirmed') or (not w['business_confirmation']) or (w['business_confirmation']['choice'] != 'confirm') or (w['business_confirmation']['root_cause_hash'] != digest(w['root_cause'])):
                    raise ValueError('根因或用户确认无效')
                if not isinstance(report.get('content'), str) or not report['content'].strip():
                    raise ValueError('PRD 内容为空')
                w['prd'] = {**report, 'hash': hashlib.sha256(report['content'].encode()).hexdigest()}
                w['prd_reviews'] = []
                if report.get('converged') is True:
                    w['stage'] = 'prd_review'
                else:
                    status = 'awaiting_prd_input'
            elif stage == 'prd_review':
                if not w['prd'] or not w['prd'].get('converged') or report.get('prd_hash') != w['prd']['hash']:
                    raise ValueError('审查必须绑定同一版 PRD')
                w['prd_reviews'].append(report)
                if report.get('converged') is True:
                    w['stage'] = 'implementation'
                    w['ready_for_implementation'] = True
                else:
                    status = 'awaiting_prd_input'
            else:
                raise ValueError('不支持的阶段结果')
            db.execute('UPDATE attempts SET status=? WHERE job_id=? AND epoch=?', (status, job_id, epoch))
            self._update_gate(db, job_id, w, status, '阶段结果已保存：' + stage)
            return True
