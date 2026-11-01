import io
import json
import time
from unittest.mock import patch
import pytest
from app import create_app
from settings import DEFAULT_MODELS, usage_cost, estimate
from test_app import first, manual, post

@pytest.fixture
def client(tmp_path):
    c=create_app(tmp_path).test_client();c.get('/')
    with c.session_transaction() as s: c.token=s['csrf']
    return c

def login(c,password='5162'):
    return post(c,'/api/admin/login',{'password':password})

def save(c,data):
    return c.put('/api/admin/settings',json=data,headers={'X-CSRF-Token':c.token})


def test_admin_auth_expiry_csrf_and_rate_limit(client):
    assert client.get('/api/admin/settings').status_code==401
    assert client.put('/api/admin/settings',json={}).status_code==403
    assert login(client).status_code==200
    with client.session_transaction() as s:s['admin_until']=time.time()-1
    assert client.get('/api/admin/settings').status_code==401
    for i in range(5): assert login(client,'wrong').status_code==401
    assert login(client).status_code==429


def test_encrypted_key_never_returned_and_cold_start(tmp_path,client):
    assert login(client).status_code==200
    key='sk-fake-unit-test-key'
    r=save(client,{'key':key})
    assert r.status_code==200
    cookie=client.get_cookie('exam_preferences')
    assert cookie.http_only and cookie.same_site=='Strict' and key not in cookie.value
    data=client.get('/api/admin/settings').json
    assert data['key_configured'] and key not in json.dumps(data)
    # Restart against the same disk secret; encrypted browser settings still open.
    c2=create_app(tmp_path).test_client()
    for name in ['session','exam_preferences']:
        c2.set_cookie(name,client.get_cookie(name).value)
    assert c2.get('/api/admin/settings').json['key_configured']
    assert client.get('/api/ai/status').json['configured']
    assert save(client,{'delete_key':True}).status_code==200
    assert not client.get('/api/admin/settings').json['key_configured']


def test_cloud_settings_survive_instance_rotation_and_isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('VERCEL','1');monkeypatch.setenv('EXAM_SESSION_SECRET','a-long-test-secret-for-cold-start-12345678')
    a=create_app(tmp_path/'a').test_client();a.get('/')
    with a.session_transaction() as s:a.token=s['csrf']
    login(a);assert save(a,{'key':'sk-fake-cloud-key'}).status_code==200
    b=create_app(tmp_path/'b').test_client()
    for name in ['session','exam_preferences']:
        b.set_cookie(name,a.get_cookie(name).value)
    assert b.get('/api/admin/settings').json['key_configured']
    stranger=create_app(tmp_path/'c').test_client();stranger.get('/')
    with stranger.session_transaction() as s:stranger.token=s['csrf']
    login(stranger)
    assert not stranger.get('/api/admin/settings').json['key_configured']


def test_password_change_logout_and_key_replacement(client):
    login(client)
    assert save(client,{'key':'sk-fake-original','new_password':'long-new-password'}).status_code==200
    assert not client.get('/api/admin/settings').json['default_password']
    assert save(client,{'key':'sk-fake-replacement'}).status_code==200
    assert post(client,'/api/admin/logout',{}).status_code==200
    assert client.get('/api/admin/settings').status_code==401
    assert login(client).status_code==401
    assert login(client,'long-new-password').status_code==200


@pytest.mark.parametrize('data',[{'model':'not-registered'},{'models':[]},{'key':'invalid'}, {'new_password':'tiny'}, {'models':[dict(DEFAULT_MODELS[0],output=-1)]}])
def test_invalid_preferences(client,data):
    login(client)
    assert save(client,data).status_code==400


def test_custom_model_and_units(client):
    login(client)
    model=dict(id='future-vision-model',name='새 모델',input=1.5,cached=0.1,output=4,note='테스트')
    assert save(client,{'models':[model],'model':model['id']}).status_code==200
    assert client.get('/api/admin/settings').json['model']==model['id']
    assert usage_cost({'prompt_tokens':1000000,'completion_tokens':100000,'prompt_tokens_details':{'cached_tokens':100000}},model)==1.76
    assert estimate(2,model)['output_tokens']==12000


def test_paid_usage_stored_for_success_and_invalid_json(client):
    login(client);save(client,{'key':'sk-fake-no-network'})
    e=first(client)
    def analyze(images,metadata,**kwargs):
        kwargs['on_usage']({'prompt_tokens':1000,'completion_tokens':2000,'prompt_tokens_details':{'cached_tokens':100}})
        return [dict(manual(),status='검토 필요',reason='AI 판단 근거')]
    with patch('app.vision_questions',side_effect=analyze):
        r=post(client,f"/api/exams/{e['id']}/analyze",{'consent':True})
    assert r.status_code==200
    b=client.get('/api/admin/settings').json['billing']
    assert b['count']==1 and b['usd']==0.00357 and b['recent'][0]['status']=='분석 완료'
    e=first(client)
    def invalid(images,metadata,**kwargs):
        kwargs['on_usage']({'prompt_tokens':1000,'completion_tokens':100})
        raise ValueError('잘못된 JSON 응답')
    with patch('app.vision_questions',side_effect=invalid):
        assert post(client,f"/api/exams/{e['id']}/analyze",{'consent':True}).status_code==400
    b=client.get('/api/admin/settings').json['billing']
    assert b['count']==2 and b['recent'][0]['usd']>0 and '실패' in b['recent'][0]['status']
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==3


def test_timeout_unknown_cost_and_admin_required(client):
    login(client);save(client,{'key':'sk-fake-no-network'});e=first(client)
    assert client.get(f"/api/exams/{e['id']}/estimate").json['model']=='gpt-4.1-mini'
    with patch('app.vision_questions',side_effect=ValueError('시간 초과')):
        assert post(client,f"/api/exams/{e['id']}/analyze",{'consent':True}).status_code==400
    b=client.get('/api/admin/settings').json['billing']
    assert b['unknown']==1 and b['recent'][0]['usd'] is None
    post(client,'/api/admin/logout',{})
    with patch('app.vision_questions') as mock:
        assert post(client,f"/api/exams/{e['id']}/analyze",{'consent':True}).status_code==401
        mock.assert_not_called()


def test_adapter_accounts_for_invalid_response(client,monkeypatch):
    from analysis import vision_questions
    called=[]
    response=io.BytesIO(json.dumps({'usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[{'message':{'content':'invalid json'}}]}).encode())
    with patch('urllib.request.urlopen',return_value=response):
        with pytest.raises(ValueError):vision_questions([b'image'],{},key='sk-fake',model='gpt-4.1-mini',on_usage=called.append)
    assert called[0]['prompt_tokens']==100


def test_billing_window_keeps_totals(client):
    from settings import record_usage,finish_usage
    with client.application.test_request_context('/'):
        for i in range(12):
            uid=record_usage('exam',DEFAULT_MODELS[0],{'prompt_tokens':1000,'completion_tokens':1000});finish_usage(uid,True)
        from flask import session
        assert session['billing']['count']==12 and len(session['billing']['recent'])==8
        assert round(session['billing']['usd'],3)==.024


def test_non_ascii_password_returns_login_error(client):
    assert login(client,'틀린비밀번호').status_code==401
    assert login(client).status_code==200
