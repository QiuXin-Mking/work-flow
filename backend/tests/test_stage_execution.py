import asyncio
from app.trellis_context import prepare_trellis_context
from app.stage_runner import validate_stage_input

def test_snapshot_is_not_a_prd(tmp_path):
    prepare_trellis_context(tmp_path, {'title':'a','description':'b','comments':[]}, ['neptune-robotics/voyager_server'])
    assert (tmp_path/'snapshot.json').exists()
    assert not (tmp_path/'.trellis/tasks/bugfix/prd.md').exists()

def test_missing_information_stops_before_repository_preparation():
    result=validate_stage_input('information', {'input':{}})
    assert result['complete'] is False
    assert 'mobi' in result['missing']

def test_implementation_requires_dual_approval():
    import pytest
    with pytest.raises(ValueError):
        validate_stage_input('implementation', {'ready_for_implementation':False})


def test_missing_information_worker_does_not_clone_or_launch(monkeypatch, tmp_path):
    from app.store import Store
    from app.worker import _run_attempt
    store = Store(tmp_path/'jobs.sqlite')
    owner = store.create_user('owner','password')
    job = store.create_job('url',['neptune-robotics/voyager_server'],owner,snapshot={'title':'bug','description':'missing input'})
    claim = store.claim_next_job('worker')
    def forbidden(*args,**kwargs):
        raise AssertionError('must not clone or launch before input completeness')
    monkeypatch.setattr('app.worker.subprocess.run', forbidden)
    _run_attempt(store,claim,{'neptune-robotics/voyager_server':{'target_branch':'dev'}},tmp_path/'attempts','image','volume')
    assert store.get_job(job)['status']=='needs_input'
    assert not list(tmp_path.rglob('prd.md'))


def test_preparation_writes_only_explicit_prd(tmp_path):
    prepare_trellis_context(tmp_path, {'title':'a','description':'b'}, ['neptune-robotics/voyager_server'],prd='approved business specification')
    assert (tmp_path/'.trellis/tasks/bugfix/prd.md').read_text()=='approved business specification'


def test_planning_stages_have_distinct_roles():
    from app.stage_runner import stage_context
    assert 'primary PRD author' in stage_context('prd')
    assert 'independent PRD convergence reviewer' in stage_context('prd_review')
    assert 'Never infer a root cause' in stage_context('information')
    assert 'write diagnosis.json' not in stage_context('prd')


def test_baseline_binding_rejects_any_analysis_repository_change():
    from app.task_runner import validate_baselines
    import pytest
    with pytest.raises(ValueError, match='baseline'):
        validate_baselines({'a/b':'old','a/c':'old'}, {'a/b':'old','a/c':'new'})


def test_approved_prd_content_must_match_hash():
    from app.task_runner import approved_prd
    import pytest
    with pytest.raises(ValueError, match='PRD'):
        approved_prd({'prd':{'content':'changed','hash':'bad'}})


def test_execution_error_does_not_expose_credentials(monkeypatch):
    from app.execution_errors import safe_error
    import subprocess
    monkeypatch.setenv('GITHUB_TOKEN','private-value')
    assert 'private-value' not in safe_error(ValueError('failed private-value'))
    assert 'private-value' not in safe_error(subprocess.CalledProcessError(1,['git','https://private-value@host']))
