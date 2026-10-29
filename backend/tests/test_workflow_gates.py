import pytest
from app.store import Store

def test_workflow_requires_information_and_bound_prd_reviews(tmp_path):
 s=Store(tmp_path/'db'); u=s.create_user('a','secret1'); j=s.create_job('url',['acme/repo'],u,{'title':'bug'})
 w=s.ensure_workflow(j)
 assert w['stage']=='information' and not w['ready_for_implementation']
 c=s.claim_next_job('worker')
 with pytest.raises(ValueError): s.record_workflow_result(j,c['epoch'],'information',{'complete':True})
 assert not s.record_workflow_result(j,c['epoch']+1,'information',{'complete':False})
 assert s.record_workflow_result(j,c['epoch'],'information',{'complete':False,'missing':['mobi'],'questions':['which mobi?']})
 assert s.get_job(j)['status']=='needs_input'
 with pytest.raises(ValueError): s.save_candidate(j,{},epoch=c['epoch'])


def test_mobi_literal_intermit_and_revision_fencing(tmp_path):
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'description':'mobi:425 trace-id:abc-123'})
 w=s.get_workflow(j);assert w['input']['mobi']=='425'
 c=s.claim_next_job('worker')
 w=s.submit_workflow_information(j,u,w['revision'],dict(mobi='425',environment='74',steps='steps',expected='ok',actual='fail',reproduction='intermittent',triggeredOn74=True,traceId='abc-123'))
 assert not s.record_workflow_result(j,c['epoch'],'information',{'complete':True})
 with pytest.raises(ValueError):s.submit_workflow_information(j,u,w['revision']-1,{})
 c=s.claim_next_job('worker');assert s.record_workflow_result(j,c['epoch'],'information',{'complete':True})
 assert s.get_workflow(j)['stage']=='root_cause'
 assert s.get_snapshot_version(j,1)['description']=='mobi:425 trace-id:abc-123'


def test_no_prd_without_root_confirmation(tmp_path):
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'title':'bug'})
 c=s.claim_next_job('worker');assert not s.record_workflow_result(j,c['epoch'],'prd',{'content':'unsafe','converged':True})
 assert s.get_workflow(j)['prd'] is None


def test_workflow_api_requires_owner_and_current_version(tmp_path):
 from fastapi.testclient import TestClient
 from app.api import create_app
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');other=s.create_user('b','secret2');j=s.create_job('url',['acme/repo'],u,{'description':'mobi:42'})
 client=TestClient(create_app(s,None,{}));client.cookies.set('session',s.create_session(other))
 body={'expectedRevision':1,'input':{'mobi':'42'}}
 assert client.post(f'/api/jobs/{j}/information',json=body).status_code==403
 client.cookies.set('session',s.create_session(u))
 assert client.post(f'/api/jobs/{j}/information',json=body).status_code==200
 assert client.post(f'/api/jobs/{j}/information',json=body).status_code==409
 assert client.get(f'/api/jobs/{j}/artifacts/prd').status_code==404
 snap=client.get(f'/api/jobs/{j}/artifacts/snapshot').json()
 assert snap['snapshotVersion']==2 and snap['data']['supplement']['mobi']=='42'
 assert client.post(f'/api/jobs/{j}/business-confirmation',json={'expectedRevision':2,'choice':'confirm','selectedChangeIds':['invented']}).status_code==409


@pytest.mark.parametrize('report',[None,[],{'complete':'true'},{'complete':None},{'complete':True,'questions':'not-a-list'}])
def test_invalid_information_report_is_rejected(tmp_path,report):
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'description':'mobi:42'})
 c=s.claim_next_job('worker')
 with pytest.raises(ValueError):s.record_workflow_result(j,c['epoch'],'information',report)
 assert s.get_workflow(j)['stage']=='information'


def test_empty_root_cause_cannot_advance(tmp_path):
 from workflow_helpers import pass_planning
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'description':'mobi:42'})
 w=s.get_workflow(j);s.submit_workflow_information(j,u,w['revision'],dict(mobi='42',environment='74',reproduction='always',steps='x',expected='ok',actual='fail'))
 c=s.claim_next_job('worker');s.record_workflow_result(j,c['epoch'],'information',{'complete':True})
 c=s.claim_next_job('worker')
 with pytest.raises(ValueError):s.record_workflow_result(j,c['epoch'],'root_cause',{'confirmed':True,'root_cause':'','evidence':{'logs':['x'],'code':['y']},'business_changes':[{'id':'x','description':'fix'}]})


def test_source_update_and_arbitrary_mobi_cannot_advance(tmp_path):
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'description':'mobi:42'})
 w=s.get_workflow(j);s.submit_workflow_information(j,u,w['revision'],dict(mobi='43',environment='74',reproduction='always',steps='x',expected='ok',actual='fail'))
 c=s.claim_next_job('worker')
 with pytest.raises(ValueError):s.record_workflow_result(j,c['epoch'],'information',{'complete':True})
 s.save_snapshot(j,{'description':'mobi:43'},epoch=c['epoch'])
 assert not s.record_workflow_result(j,c['epoch'],'information',{'complete':True})



def test_refresh_binds_latest_source_and_fences_previous_run(tmp_path):
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'description':'missing info'})
 claim=s.claim_next_job('worker')
 w=s.refresh_workflow_snapshot(j,u,1,{'description':'mobi:42 trace-id:new-trace'})
 assert w['source_snapshot_version']==2 and w['input']['traceId']=='new-trace'
 assert s.get_snapshot_version(j,1)['description']=='missing info'
 assert not s.record_workflow_result(j,claim['epoch'],'information',{'complete':False})
 w=s.submit_workflow_information(j,u,w['revision'],dict(mobi='42',environment='74',reproduction='intermittent',steps='x',expected='ok',actual='fail',triggeredOn74=True,traceId='new-trace'))
 assert w['source_snapshot_version']==2 and w['snapshot_version']==3
 claim=s.claim_next_job('worker');assert s.record_workflow_result(j,claim['epoch'],'information',{'complete':True})
 s.set_job_status(j,'failed')
 assert s.retry_job(j,u)
 claim=s.claim_next_job('worker');assert s.record_workflow_result(j,claim['epoch'],'information',{'complete':True})


def test_refresh_endpoint_reads_feishu_not_client_snapshot(tmp_path):
 from fastapi.testclient import TestClient
 from app.api import create_app
 from app.feishu import BugSnapshot
 class Source:
  def get_bug(self,ref):return BugSnapshot(ref,'bug','mobi:42 trace-id:fresh',123,{})
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');url='https://project.feishu.cn/acme/issue/detail/123'
 j=s.create_job(url,['acme/repo'],u,{'description':'old'})
 client=TestClient(create_app(s,Source(),{}));client.cookies.set('session',s.create_session(u))
 r=client.post(f'/api/jobs/{j}/refresh-snapshot',json={'expectedRevision':1})
 assert r.status_code==200,r.text
 assert r.json()['workflow']['input']['traceId']=='fresh'
 assert client.post(f'/api/jobs/{j}/refresh-snapshot',json={'expectedRevision':1}).status_code==409


@pytest.mark.parametrize('stage,report',[
 ('information',{'complete':True,'missing':['mobi']}),
 ('information',{'complete':True,'questions':['Which environment?']}),
 ('root_cause',{'confirmed':True,'questions':['cause unknown']}),
 ('prd',{'converged':True,'content':'draft','questions':['behavior undecided']}),
 ('prd_review',{'converged':True,'prd_hash':'hash','questions':['scope unresolved']})])
def test_contradictory_pass_reports_are_rejected(tmp_path,stage,report):
 from app.workflow import validate_report
 with pytest.raises(ValueError):validate_report(stage,report)



def test_contradictory_information_does_not_advance_database_stage(tmp_path):
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');j=s.create_job('url',['acme/repo'],u,{'description':'mobi:42'})
 w=s.get_workflow(j);s.submit_workflow_information(j,u,w['revision'],dict(mobi='42',environment='74',reproduction='always',steps='x',expected='ok',actual='fail'))
 claim=s.claim_next_job('worker')
 with pytest.raises(ValueError):s.record_workflow_result(j,claim['epoch'],'information',{'complete':True,'questions':['Unresolved behavior']})
 assert s.get_workflow(j)['stage']=='information'
 assert s.get_workflow(j)['information'] is None


def test_legacy_approved_candidate_is_not_delivered(tmp_path):
 from workflow_helpers import pass_planning
 s=Store(tmp_path/'db');u=s.create_user('a','secret1');reviewer=s.create_user('b','secret2')
 j=s.create_job('url',['acme/repo'],u,{'description':'mobi:42'})
 claim=pass_planning(s,j)
 candidate=s.save_candidate(j,{'acme/repo':{'tree_id':'abc','target_branch':'dev','target_sha':'def'}},epoch=claim['epoch'])
 assert s.approve_candidate(j,candidate,reviewer)
 assert s.next_approved_candidate()==candidate
 with s._db() as db:db.execute('DELETE FROM job_workflows WHERE job_id=?',(j,))
 assert not s.approve_candidate(j,candidate,reviewer)
 assert s.next_approved_candidate() is None
