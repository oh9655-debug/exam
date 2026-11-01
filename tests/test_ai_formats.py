import io
import json
from unittest.mock import patch
import pytest
from app import create_app
from analysis import normalize_ai_question, FACTOR_NAMES
from test_app import first,post,manual
from test_settings import login,save

@pytest.fixture
def client(tmp_path):
    c=create_app(tmp_path).test_client();c.get('/')
    with c.session_transaction() as s:c.token=s['csrf']
    login(c);save(c,{'key':'sk-fake-types-no-paid-call'})
    return c


def test_number_lists_nulls_and_structured_evidence():
    q=normalize_ai_question(dict(manual(),number=1,concepts=['피타고라스 정리','닮음'],achievement=None,warning=None,reason=['조건 해석 2점','풀이 과정 2점'],factors=['2',2.0,2,2,2],points='3.5점',page_start='1',page_end='1'))
    assert q['number']=='1' and q['concepts']=='피타고라스 정리\n닮음'
    assert q['achievement']=='' and q['points']==3.5
    assert q['factors']==[2]*5 and q['page_start']==1
    assert q['status']=='검토 필요' and q['_raw_response']['concepts']==['피타고라스 정리','닮음']
    assert '조건 해석 2점' in q['reason']


def test_dictionary_factors_and_no_evidence_not_rated():
    q=normalize_ai_question(dict(manual(),factors=dict(zip(FACTOR_NAMES,[2]*5)),reason={'풀이':'두 개념을 연결'}))
    assert q['factors']==[2]*5 and '두 개념을 연결' in q['reason']
    q=normalize_ai_question(dict(manual(),reason=None))
    assert q['factors'] is None and '근거 없음' in q['warning']


def test_25_heterogeneous_questions_one_call_and_usage(client):
    e=first(client)
    questions=[dict(manual(),number=i,concepts=['삼각형','닮음'],achievement=None,warning=None,reason=['여러 조건 해석','관련 개념 연결'],points='4점',factors=['2']*5) for i in range(1,26)]
    reply={'usage':{'prompt_tokens':1000,'completion_tokens':2000},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'questions':questions},ensure_ascii=False)}}]}
    with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(reply).encode())) as mock:
        result=post(client,f"/api/exams/{e['id']}/analyze",{'consent':True})
        assert result.status_code==200,result.json
        mock.assert_called_once()
        payload=json.loads(mock.call_args.args[0].data)
        assert payload['response_format']['type']=='json_schema'
        schema=payload['response_format']['json_schema']
        assert schema['strict'] and schema['schema']['properties']['questions']['items']['additionalProperties'] is False
    data=client.get('/api/exams/'+e['id']).json
    assert len(data['questions'])==25
    assert sum(q['points'] for q in data['questions'])==100
    assert all(q['score']==50 and q['status']=='검토 필요' for q in data['questions'])
    assert data['questions'][0]['ai_snapshot']['raw_response']['number']==1
    billing=client.get('/api/admin/settings').json['billing']
    assert billing['count']==1 and billing['recent'][0]['status']=='분석 완료'
    assert client.get('/api/exams/'+e['id']).json['questions'][24]['number']=='25'


def test_invalid_result_draft_and_free_recovery(client):
    e=first(client)
    invalid=dict(manual(),number=1,page_end=99,reason='판단 근거')
    def fake(images,meta,**kwargs):
        kwargs['on_usage']({'prompt_tokens':100,'completion_tokens':100})
        return [invalid]
    with patch('app.vision_questions',side_effect=fake) as paid:
        r=post(client,f"/api/exams/{e['id']}/analyze",{'consent':True})
        assert r.status_code==400 and r.json['analysis_draft']['questions'][0]['page_end']==99
        paid.assert_called_once()
    assert len(client.get('/api/exams/'+e['id']).json['questions'])==3
    draft=r.json['analysis_draft'];draft['questions'][0]['page_end']='1'
    with patch('app.vision_questions') as paid:
        r=post(client,f"/api/exams/{e['id']}/recover",draft)
        assert r.status_code==200 and r.json['recovered']
        paid.assert_not_called()
    assert client.get('/api/admin/settings').json['billing']['count']==1
    assert client.get('/api/exams/'+e['id']).json['questions'][0]['number']=='1'


def test_teacher_invalid_edit_is_still_rejected(client):
    e=first(client);qid=e['questions'][0]['id']
    response=client.patch('/api/questions/'+qid,json={'concepts':['user list']},headers={'X-CSRF-Token':client.token})
    assert response.status_code==400 and 'concepts' in response.json['error']


def test_workspace_error_preserves_recoverable_draft(client):
    from test_workspace import workspace
    e=first(client)
    def bad(images,metadata,**kwargs):
        kwargs['on_usage']({'prompt_tokens':100,'completion_tokens':100})
        return [dict(manual(),number=1,page_end=99,reason='검증용 근거')]
    with patch('app.vision_questions',side_effect=bad):
        r=workspace(client,e,f"/api/exams/{e['id']}/analyze",'POST',{'consent':True})
        assert r.status_code==400 and 'analysis_draft' in r.json and 'error' in r.json
    draft=r.json['analysis_draft'];draft['questions'][0]['page_end']=1
    with patch('app.vision_questions') as paid:
        r=workspace(client,e,f"/api/exams/{e['id']}/recover",'POST',draft)
        assert r.status_code==200 and r.json['exam']['mode']=='ai'
        paid.assert_not_called()
