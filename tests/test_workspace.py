import io
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from app import create_app
from test_app import manual

@pytest.fixture
def instances(tmp_path,monkeypatch):
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.setenv('EXAM_SESSION_SECRET','unit-test-cold-instance-secret-at-least-32chars')
    count=0;previous=None
    def new():
        nonlocal count,previous
        count+=1
        client=create_app(tmp_path/str(count)).test_client()
        if previous:
            for name in ['session','exam_preferences']:
                cookie=previous.get_cookie(name)
                if cookie:client.set_cookie(name,cookie.value)
        client.get('/')
        with client.session_transaction() as s:client.token=s['csrf']
        previous=client
        return client
    return new


def sample():return (Path(__file__).resolve().parents[1]/'samples/sample-text.pdf').read_bytes()


def upload(c):
    return c.post('/api/exams',headers={'X-CSRF-Token':c.token},data=dict(school='서버 교체 검증',grade='1',year='2026',semester='1',file=(io.BytesIO(sample()),'sample.pdf')),content_type='multipart/form-data')


def workspace(c,state,path,method='GET',payload=None):
    return c.post('/api/workspace',headers={'X-CSRF-Token':c.token},data=dict(state=json.dumps(state),path=path,method=method,payload=json.dumps(payload),file=(io.BytesIO(sample()),'transfer.pdf')),content_type='multipart/form-data')


def test_each_operation_runs_on_fresh_instance(instances):
    a=instances();r=upload(a);assert r.status_code==201
    state=r.json['exam'];eid=state['id'];qid=state['questions'][0]['id']
    b=instances()
    assert b.get('/api/exams/'+eid).status_code==404 # reproduce original defect
    r=workspace(b,state,'/api/exams/'+eid)
    assert r.status_code==200 and len(r.json['exam']['questions'])==3
    state=r.json['exam']
    c=instances();r=workspace(c,state,'/api/questions/'+qid,'PATCH',dict(manual(),number='1',reason='서버 교체 뒤 교사 판정'))
    assert r.status_code==200,r.json
    state=r.json['exam'];assert state['questions'][0]['reason']=='서버 교체 뒤 교사 판정'
    d=instances();r=workspace(d,state,'/api/questions/'+qid+'/history')
    assert r.status_code==200 and r.json['result'][0]['after']['score']==25
    state=r.json['exam']
    e=instances();r=workspace(e,state,f'/api/exams/{eid}/pages/1.png')
    assert r.status_code==200 and r.data.startswith(b'\x89PNG')
    f=instances();r=workspace(f,state,f'/api/exams/{eid}/questions','POST',manual())
    assert r.status_code==201 and len(r.json['exam']['questions'])==4
    state=r.json['exam'];added=r.json['result']['id']
    g=instances();r=workspace(g,state,'/api/questions/'+added,'DELETE')
    assert r.status_code==200 and len(r.json['exam']['questions'])==3


def test_fresh_instance_analysis_requires_auth_and_carries_usage(instances):
    a=instances()
    a.post('/api/admin/login',json={'password':'5162'},headers={'X-CSRF-Token':a.token})
    a.put('/api/admin/settings',json={'key':'sk-fake-no-paid-api'},headers={'X-CSRF-Token':a.token})
    state=upload(a).json['exam'];eid=state['id']
    b=instances()
    assert workspace(b,state,f'/api/exams/{eid}/estimate').status_code==200
    def fake(images,meta,**kw):
        kw['on_usage']({'prompt_tokens':100,'completion_tokens':200})
        return [{**manual(),'status':'검토 필요','reason':'AI 모형 근거'}]
    with patch('app.vision_questions',side_effect=fake) as mock:
        r=workspace(b,state,f'/api/exams/{eid}/analyze','POST',{'consent':True})
        assert r.status_code==200,r.json
        mock.assert_called_once()
    state=r.json['exam'];assert state['mode']=='ai'
    assert state['questions'][0]['ai_snapshot']['reason']=='AI 모형 근거'
    c=instances();assert c.get('/api/admin/settings').json['billing']['count']==1
    r=workspace(c,state,f'/api/exams/{eid}')
    assert r.status_code==200 and r.json['exam']['mode']=='ai'


def test_workspace_rejects_paths_and_mismatched_exam(instances):
    c=instances();state=upload(c).json['exam']
    assert workspace(c,state,'/api/admin/settings','GET').status_code==400
    assert workspace(c,state,'/api/exams/'+'0'*32).status_code==400
    state['id']='../escape'
    assert workspace(c,state,'/api/exams/'+'0'*32).status_code==400
