"""Authenticated workflow decisions and versioned artifact access."""
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict

class InformationFields(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mobi: str=''
    environment: str=''
    reproduction: Literal['always','intermittent','unknown']='unknown'
    steps: str=''
    expected: str=''
    actual: str=''
    traceId: str=''
    triggeredOn74: bool=False
    occurredAt: str=''
    notes: str=''

class InformationInput(BaseModel):
    expectedRevision:int=Field(ge=1)
    input:InformationFields

class BusinessInput(BaseModel):
    expectedRevision:int=Field(ge=1)
    choice:Literal['confirm','modify','reject']
    selectedChangeIds:list[str]=Field(default_factory=list)
    comment:str=''

class PrdFeedback(BaseModel):
    expectedRevision:int=Field(ge=1)
    answer:str=Field(min_length=1)


def install_workflow_routes(app,store,current_user,present_job,read_bug,snapshot_dict):
    def mutate(action):
        try: return action()
        except KeyError as exc:raise HTTPException(404,'任务不存在') from exc
        except PermissionError as exc:raise HTTPException(403,'只有任务发起人可以操作') from exc
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    class RefreshSnapshot(BaseModel):
        expectedRevision:int=Field(ge=1)

    @app.post('/api/jobs/{job_id}/refresh-snapshot')
    def refresh_snapshot(job_id:str,body:RefreshSnapshot,user=Depends(current_user)):
        job=store.get_job(job_id)
        if job is None: raise HTTPException(404,'任务不存在')
        if job['created_by']!=user['id']:raise HTTPException(403,'只有任务发起人可以操作')
        if not job.get('workflow') or job['workflow']['revision']!=body.expectedRevision:raise HTTPException(409,'工作流版本已变化，请刷新')
        snapshot=read_bug(job['url'])
        if snapshot.source_url!=job['url']:raise HTTPException(409,'工单身份发生变化')
        if snapshot.status and snapshot.status.casefold() in {'resolved','closed','done','已修复','已关闭','已完成'}:raise HTTPException(409,'工单已修复或关闭')
        mutate(lambda:store.refresh_workflow_snapshot(job_id,user['id'],body.expectedRevision,snapshot_dict(snapshot)))
        return present_job(store.get_job(job_id))

    @app.post('/api/jobs/{job_id}/information')
    def information(job_id:str,body:InformationInput,user=Depends(current_user)):
        mutate(lambda:store.submit_workflow_information(job_id,user['id'],body.expectedRevision,body.input.model_dump()))
        return present_job(store.get_job(job_id))

    @app.post('/api/jobs/{job_id}/business-confirmation')
    def business(job_id:str,body:BusinessInput,user=Depends(current_user)):
        mutate(lambda:store.confirm_business(job_id,user['id'],body.expectedRevision,body.choice,body.selectedChangeIds,body.comment))
        return present_job(store.get_job(job_id))

    @app.post('/api/jobs/{job_id}/prd-feedback')
    def prd(job_id:str,body:PrdFeedback,user=Depends(current_user)):
        mutate(lambda:store.submit_prd_feedback(job_id,user['id'],body.expectedRevision,body.answer))
        return present_job(store.get_job(job_id))

    @app.get('/api/jobs/{job_id}/artifacts/{name}')
    def artifact(job_id:str,name:str,user=Depends(current_user)):
        job=store.get_job(job_id)
        if job is None:raise HTTPException(404,'任务不存在')
        w=job.get('workflow') or {}
        if name=='snapshot':data=store.get_snapshot_version(job_id,w.get('snapshot_version',1))
        elif name in ('information','root-cause','prd','prd-reviews'):data=w.get(name.replace('-','_'))
        else:raise HTTPException(404,'产物不存在')
        if data is None:raise HTTPException(404,'产物尚未生成')
        return {'name':name,'snapshotVersion':w.get('snapshot_version'),'revision':w.get('revision'),'data':data}
