"""Create authentic planning state for candidate-focused tests."""
def pass_planning(store, job_id):
    job=store.get_job(job_id)
    w=store.get_workflow(job_id)
    store.submit_workflow_information(job_id,job['created_by'],w['revision'],dict(mobi='42',environment='74',reproduction='always',steps='click',expected='success',actual='failure'))
    def result(stage,report):
        claim=store.claim_next_job('fixture')
        assert claim['job_id']==job_id
        assert store.record_workflow_result(job_id,claim['epoch'],stage,report)
    result('information',{'complete':True})
    result('root_cause',{'confirmed':True,'root_cause':'known cause','evidence':{'logs':['log evidence'],'code':['file:1']},'business_changes':[{'id':'fix','description':'fix behavior'}]})
    w=store.get_workflow(job_id)
    store.confirm_business(job_id,job['created_by'],w['revision'],'confirm',['fix'])
    result('prd',{'converged':True,'content':'Confirmed PRD'})
    result('prd_review',{'converged':True,'prd_hash':store.get_workflow(job_id)['prd']['hash']})
    return store.claim_next_job('fixture')
