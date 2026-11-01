import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import fitz
import pytest
from app import create_app
from analysis import difficulty, local_questions, vision_questions

@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)

@pytest.fixture
def client(app):
    c = app.test_client()
    c.get('/')
    with c.session_transaction() as s:
        c.token = s['csrf']
    return c

def post(c,url,data=None,**kwargs):
    if kwargs.get('content_type') == 'multipart/form-data':
        return c.post(url,headers={'X-CSRF-Token':c.token},data=data,**kwargs)
    return c.post(url,headers={'X-CSRF-Token':c.token},json=data,**kwargs)

def upload(c,kind='pdf'):
    folder = Path(__file__).resolve().parents[1]/'samples'
    file = {'pdf':'sample-text.pdf','scan':'sample-scan.pdf','png':'sample-image.png'}[kind]
    data = dict(school='테스트중',grade='1',year='2026',semester='1',exam_type='중간고사',scope='방정식·함수',curriculum='2022 개정',file=(io.BytesIO((folder/file).read_bytes()),file))
    return post(c,'/api/exams',data=data,content_type='multipart/form-data')

def first(c):
    response = upload(c)
    assert response.status_code == 201, response.json
    return c.get('/api/exams/'+response.json['id']).json

def manual():
    return dict(number='4',text='직접 입력 문제',page_start=1,page_end=1,points=5,unit='문자와 식',form='단답형',kind='계산',factors=[1,1,1,1,1],status='교사 검토 완료')

@pytest.mark.parametrize('f,score,level', [([0]*5,0,'최하'),([1]*5,25,'하'),([2]*5,50,'중'),([3]*5,75,'상'),([4]*5,100,'최상'),([0,0,4,0,0],20,'하'),([0,4,2,0,0],40,'중'),([0,4,0,0,0],30,'하'),([4,4,1,0,0],60,'상'),([4,4,4,2,0],80,'최상')])
def test_difficulty(f,score,level):
    assert difficulty(f)==(score,level)

@pytest.mark.parametrize('f', [[5]*5,[-1]*5,[True]*5,[1,2],['1']*5])
def test_invalid_factors(f):
    with pytest.raises(ValueError): difficulty(f)

def test_extraction_continuation():
    q=local_questions(['1. 방정식 x=2 [4점]\n2. 함수 y=2x [5점]', '(2) 계속되는 소문항'])
    assert len(q)==2 and q[1]['page_end']==2
    assert '(2)' in q[1]['text'] and q[0]['points']==4
    assert q[0]['factors'] is None and q[0]['status']=='검토 필요'

def test_upload_preview_and_original(client):
    e=first(client)
    assert len(e['questions'])==3
    assert e['questions'][-1]['page_end']==2
    assert e['questions'][0]['points']==4
    assert e['questions'][0]['unit']=='문자와 식'
    assert e['mode']=='local'
    assert client.get(f"/api/exams/{e['id']}/pages/1.png").data.startswith(b'\x89PNG')
    assert client.get(f"/api/exams/{e['id']}/original").data.startswith(b'%PDF-')
    assert client.get(f"/api/exams/{e['id']}/pages/99.png").status_code==404

@pytest.mark.parametrize('kind',['png','scan'])
def test_image_requires_api_or_manual(client,kind):
    r=upload(client,kind)
    assert r.status_code==201
    e=client.get('/api/exams/'+r.json['id']).json
    assert e['questions']==[]
    assert '수동' in e['notice']
    assert client.get(f"/api/exams/{e['id']}/pages/1.png").status_code==200

def test_jpg(client):
    pix=fitz.Pixmap(str(Path(__file__).resolve().parents[1]/'samples/sample-image.png'))
    r=post(client,'/api/exams',data=dict(school='테스트',grade='2',year='2026',semester='2',file=(io.BytesIO(pix.tobytes('jpeg')),'file.jpg')),content_type='multipart/form-data')
    assert r.status_code==201

def test_reopen_and_edit_history(client,tmp_path):
    e=first(client);q=e['questions'][0];id=q['id']
    value={**q,**manual(), 'number':'1'}
    r=client.patch('/api/questions/'+id,json=value,headers={'X-CSRF-Token':client.token})
    assert r.status_code==200
    # Create a new application instance against the same disk database.
    c=create_app(tmp_path).test_client()
    updated=c.get('/api/exams/'+e['id']).json['questions'][0]
    assert updated['score']==25 and updated['level']=='하'
    assert updated['status']=='교사 검토 완료'
    history=c.get('/api/questions/'+id+'/history').json
    assert history[0]['before']['score'] is None and history[0]['after']['score']==25
    assert c.get('/api/exams').json[0]['id']==e['id']

def test_teacher_override_and_validation(client):
    e=first(client);qid=e['questions'][0]['id']
    r=client.patch('/api/questions/'+qid,json={**manual(),'override_level':'상'},headers={'X-CSRF-Token':client.token})
    assert r.status_code==200
    q=client.get('/api/exams/'+e['id']).json['questions'][0]
    assert q['score']==25 and q['override_level']=='상'
    r=client.patch('/api/questions/'+qid,json={'points':-1},headers={'X-CSRF-Token':client.token})
    assert r.status_code==400
    r=client.patch('/api/questions/'+qid,json={'page_end':100},headers={'X-CSRF-Token':client.token})
    assert r.status_code==400

def test_manual_add_delete(client):
    e=first(client)
    r=post(client,f"/api/exams/{e['id']}/questions",manual())
    assert r.status_code==201
    qid=r.json['id']
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==4
    assert client.delete('/api/questions/'+qid,headers={'X-CSRF-Token':client.token}).status_code==200
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==3

def test_csrf_and_host(client):
    assert client.post('/api/exams').status_code==403
    assert client.get('/',base_url='http://evil.example').status_code==403
    assert client.get('/').headers['X-Content-Type-Options']=='nosniff'

def test_bad_upload(client):
    assert post(client,'/api/exams',data=dict(school='테스트',grade='1',year='2026',semester='1',file=(io.BytesIO(b'fake'),'x.pdf')),content_type='multipart/form-data').status_code==400
    assert post(client,'/api/exams').status_code==400

def test_ai_consent_key_and_success(client,monkeypatch):
    e=first(client);url=f"/api/exams/{e['id']}/analyze"
    assert post(client,url,{}).status_code==400
    monkeypatch.delenv('EXAM_OPENAI_API_KEY',raising=False)
    assert post(client,url,{'consent':True}).status_code==400
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==3
    with patch('app.vision_questions',return_value=[{**manual(),'source':'ai','status':'검토 필요','reason':'단일 개념 기본 적용이므로 각 요소 1점','warning':'원본 대조 필요'}]) as mock:
        assert post(client,url,{'consent':True}).status_code==200
        assert len(mock.call_args.args[0])==2
    q=client.get('/api/exams/'+e['id']).json['questions'][0]
    assert q['source']=='ai' and q['ai_snapshot']['reason'] and q['score']==25
    with patch('app.vision_questions') as mock:
        assert post(client,url,{'consent':True}).status_code==400
        mock.assert_not_called()

def test_ai_rejects_invalid_response_preserves_original(client):
    e=first(client)
    with patch('app.vision_questions',return_value=[{**manual(),'factors':[99]*5}]):
        r=post(client,f"/api/exams/{e['id']}/analyze",{'consent':True})
        assert r.status_code==400
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==3

def test_ai_does_not_overwrite_edits(client):
    e=first(client);q=e['questions'][0]
    client.patch('/api/questions/'+q['id'],json={'reason':'교사 수정'},headers={'X-CSRF-Token':client.token})
    with patch('app.vision_questions') as mock:
        assert post(client,f"/api/exams/{e['id']}/analyze",{'consent':True}).status_code==400
        mock.assert_not_called()

def test_adapter_offline_contract(monkeypatch):
    monkeypatch.setenv('EXAM_OPENAI_API_KEY','fake-test-key')
    body={'choices':[{'message':{'content':json.dumps({'questions':[{**manual(),'reason':'요소별 기본 적용 판단 근거'}]})}}]}
    response=io.BytesIO(json.dumps(body).encode())
    with patch('urllib.request.urlopen',return_value=response) as mock:
        qs=vision_questions([b'fake-image'],{'grade':'1'})
        req=mock.call_args.args[0]
        payload=json.loads(req.data)
        assert req.full_url=='https://api.openai.com/v1/chat/completions'
        assert payload['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
        assert qs[0]['status']=='검토 필요'


def test_ai_snapshot_survives_teacher_edit(client):
    e=first(client)
    with patch('app.vision_questions',return_value=[{**manual(),'status':'검토 필요','reason':'AI 최초 근거'}]):
        assert post(client,f"/api/exams/{e['id']}/analyze",{'consent':True}).status_code==200
    q=client.get('/api/exams/'+e['id']).json['questions'][0]
    assert client.patch('/api/questions/'+q['id'],json={'reason':'교사 수정 근거','override_level':'최상'},headers={'X-CSRF-Token':client.token}).status_code==200
    q=client.get('/api/exams/'+e['id']).json['questions'][0]
    assert q['reason']=='교사 수정 근거' and q['ai_snapshot']['reason']=='AI 최초 근거'
    assert q['override_level']=='최상' and q['ai_snapshot']['level']=='하'


def test_api_error_explained_without_destroying_questions(client,monkeypatch):
    import urllib.error
    monkeypatch.setenv('EXAM_OPENAI_API_KEY','fake-test-key')
    e=first(client)
    with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://api.openai.com',401,'unauthorized',{},None)):
        r=post(client,f"/api/exams/{e['id']}/analyze",{'consent':True})
        assert r.status_code==400 and '인증' in r.json['error']
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==3


def test_vercel_preview_isolation_and_api_disabled(tmp_path,monkeypatch):
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.setenv('EXAM_SESSION_SECRET','test-only-random-session-key-32chars')
    app=create_app(tmp_path)
    a=app.test_client(); b=app.test_client()
    for c in [a,b]:
        assert c.get('/',base_url='https://preview.vercel.app').status_code==200
    # Test client cookie domain must match subsequent requests.
    for c in [a,b]:
        c.get('/')
        with c.session_transaction() as s: c.token=s['csrf']
    e=first(a)
    assert b.get('/api/exams').json==[]
    assert b.get('/api/exams/'+e['id']).status_code==404
    assert a.get(f"/api/exams/{e['id']}/pages/1.png").status_code==200
    with patch('app.vision_questions') as mock:
        assert post(a,f"/api/exams/{e['id']}/analyze",{'consent':True}).status_code==403
        mock.assert_not_called()
    assert app.config['MAX_CONTENT_LENGTH']==4*1024*1024


def test_vercel_requires_stable_secret(tmp_path,monkeypatch):
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.delenv('EXAM_SESSION_SECRET',raising=False)
    with pytest.raises(RuntimeError): create_app(tmp_path)
