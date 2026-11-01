"""Conservative local extraction and explicitly enabled vision analysis."""
import base64
import json
import math
import os
import re
import urllib.request
import urllib.error

WEIGHTS = [25, 30, 20, 10, 15]
LEVELS = ['최하', '하', '중', '상', '최상']
FACTOR_NAMES = ['개념의 복잡성', '사고력·추론', '풀이 과정', '계산 복잡성', '조건 해석']


def difficulty(factors):
    if not isinstance(factors, list) or len(factors) != 5 or any(type(x) is not int or not 0 <= x <= 4 for x in factors):
        raise ValueError('평가 요소는 각각 0~4의 정수 5개여야 합니다.')
    score = round(sum(x / 4 * w for x, w in zip(factors, WEIGHTS)), 2)
    return score, LEVELS[min(int(score // 20), 4)]


def local_questions(pages):
    # Only explicit line-start numbers are split. Continuation pages attach to the last item.
    questions = []
    for page, text in enumerate(pages, 1):
        matches = list(re.finditer(r'(?m)^\s*(\d{1,3})[.．)]\s+', text))
        if not matches:
            if questions and text.strip():
                questions[-1]['text'] += '\n' + text.strip()
                questions[-1]['page_end'] = page
            continue
        if questions and text[:matches[0].start()].strip():
            questions[-1]['text'] += '\n' + text[:matches[0].start()].strip()
            questions[-1]['page_end'] = page
        for i, match in enumerate(matches):
            body = text[match.end():matches[i+1].start() if i+1 < len(matches) else len(text)].strip()
            points = re.search(r'[\[(（]\s*(\d+(?:\.\d+)?)\s*점\s*[\])）]', body)
            unit = '미분류'
            for words, label in [(['방정식', '일차식'], '문자와 식'), (['함수', '좌표'], '함수'), (['삼각형', '원', '각도', '도형'], '기하'), (['확률', '평균', '자료'], '확률과 통계'), (['정수', '소수', '약수'], '수와 연산')]:
                if any(w in body for w in words):
                    unit = label
                    break
            questions.append(dict(number=match[1], text=body, page_start=page, page_end=page,
                points=float(points[1]) if points else None, unit=unit, subunit='', achievement='',
                form='객관식' if any(c in body for c in '①②③④⑤') else '미확인', kind='미분류', concepts='', feature='',
                factors=None, level=None, score=None, reason='로컬 규칙 기반 예비 분류입니다. 단원은 키워드로 제안했으며 난이도와 풀이를 판단하지 않았습니다.',
                source='local', status='검토 필요', warning='문항 경계·수식·배점을 원본과 대조하세요. 번호 없는 문제와 다단 편집은 수동 보완이 필요합니다.'))
    return questions


def normalize_ai_question(raw):
    """Normalize representation only; never invent mathematics or difficulty."""
    if not isinstance(raw,dict): raise ValueError('AI 문항 항목이 객체가 아닙니다.')
    q=dict(raw); notes=[]
    def text(value):
        if value is None:return ''
        if isinstance(value,str):return value
        if isinstance(value,list):return '\n'.join(text(x) for x in value)
        if isinstance(value,dict):return '\n'.join(str(k)+': '+text(v) for k,v in value.items())
        if type(value) in (int,float) and math.isfinite(value):return str(int(value)) if float(value).is_integer() else str(value)
        return json.dumps(value,ensure_ascii=False)
    for key in ['number','text','unit','subunit','achievement','form','kind','concepts','feature','reason','warning']:
        value=q.get(key)
        if not isinstance(value,str):notes.append(key)
        q[key]=text(value)
    if not q['number'].strip():q['number']='미확인';notes.append('문항 번호 미확인')
    if not q['text'].strip():
        q['text']='[원문 인식 실패: 원본 시험지에서 문항 내용을 확인하세요.]'
        q['factors']=None;notes.append('원문 인식 실패 · 난이도 미평가')
    q['unit']=q['unit'] or '미분류';q['kind']=q['kind'] or '미분류';q['form']=q['form'] or '미확인'
    for key in ['page_start','page_end']:
        value=q.get(key)
        if isinstance(value,str) and value.strip().isdigit():q[key]=int(value);notes.append(key)
        elif type(value) is float and value.is_integer():q[key]=int(value);notes.append(key)
    if isinstance(q.get('points'),str):
        value=q['points'].strip()
        match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*점?',value)
        if match:q['points']=float(match[1]);notes.append('points')
        elif value in ('','미확인','알 수 없음'):q['points']=None;notes.append('배점 미확인')
    factors=q.get('factors')
    if isinstance(factors,dict) and all(k in factors for k in FACTOR_NAMES):
        factors=[factors[k] for k in FACTOR_NAMES];notes.append('factors')
    if isinstance(factors,list):
        converted=[]
        for x in factors:
            if isinstance(x,str) and re.fullmatch('[0-4]',x):converted.append(int(x));notes.append('factors')
            elif type(x) is float and x.is_integer():converted.append(int(x));notes.append('factors')
            else:converted.append(x)
        q['factors']=converted
    elif factors is None:q['factors']=None
    if q.get('factors') is not None:difficulty(q['factors'])
    if q.get('factors') is not None and not q['reason'].strip():
        q['factors']=None;notes.append('평가 근거 없음 · 난이도 미평가')
    q['source']='ai';q['status']='검토 필요';q.pop('override_level',None)
    if notes:
        q['warning']=(q['warning']+'\n응답 형식 정리: '+', '.join(dict.fromkeys(notes))+'. 원본과 대조해 주세요.').strip()
        q['_raw_response']=raw
    if not q['warning']:q['warning']='AI 제안입니다. 원본 및 풀이를 교사가 확인하세요.'
    return q


def question_schema(page_count):
    properties={name:{'type':'string'} for name in ['number','text','unit','subunit','achievement','form','kind','concepts','feature','reason','warning']}
    properties['form']['enum']=['객관식','단답형','서술형','미확인']
    properties['kind']['enum']=['개념','계산','응용','추론','미분류']
    properties.update(page_start={'type':'integer','minimum':1,'maximum':page_count},page_end={'type':'integer','minimum':1,'maximum':page_count},points={'type':['number','null'],'minimum':0,'maximum':100},factors={'anyOf':[{'type':'array','items':{'type':'integer','minimum':0,'maximum':4},'minItems':5,'maxItems':5},{'type':'null'}]})
    question={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    return {'type':'json_schema','json_schema':{'name':'exam_questions','strict':True,'schema':{'type':'object','properties':{'questions':{'type':'array','items':question}},'required':['questions'],'additionalProperties':False}}}


def vision_questions(images, metadata, *, key=None, model=None, on_usage=None):
    key = key or os.environ.get('EXAM_OPENAI_API_KEY', '')
    if not key:
        raise ValueError('설정 메뉴에서 OpenAI API 키를 등록하세요.')
    instructions = '''중학교 수학 시험지를 분석한다. 원본의 수식과 그림을 추측하거나 고치지 말 것.
불명확한 부분은 [인식 불확실]로 표시한다. 여러 페이지에 걸친 문항은 연결한다.
일반적인 해당 학년 학생의 교육과정 내 자연스러운 풀이 기준으로 평가한다.
계산량만으로 추론 점수를 높이지 않는다. 교육과정 밖 개념은 warning에 표시한다.
JSON 객체 {"questions": [...]}만 반환한다. 문항마다 number(문자열), text(원문 전사),
page_start, page_end(1부터), points(미확인이면 null), unit(대단원), subunit(중·소단원),
achievement(확인된 성취기준만, 모르면 빈 문자열), form(객관식/단답형/서술형/미확인),
kind(개념/계산/응용/추론/미분류), concepts, feature, reason(풀이와 평가요소별 판단 근거),
warning(인식 또는 교육과정 불확실성), factors(개념 복잡성, 추론, 풀이 과정, 계산 복잡성, 조건 해석 순서의 0~4 정수 5개; 판단 불가이면 null)를 포함.
소문항은 text에 보존. 모든 결과는 교사 검토 전 제안이다. 시험 정보: '''
    content = [{'type': 'text', 'text': instructions + json.dumps(metadata, ensure_ascii=False)}]
    content += [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(p).decode()}} for p in images]
    payload = dict(model=model or os.environ.get('EXAM_OPENAI_MODEL', 'gpt-4.1-mini'), messages=[{'role': 'user', 'content': content}], response_format={'type': 'json_object'})
    if payload['model'] in ('gpt-4.1-mini','gpt-4.1','gpt-5-mini'):
        payload['response_format']=question_schema(len(images))
    payload['max_completion_tokens'] = 12000
    if payload['model'].startswith('gpt-5'):
        payload['reasoning_effort'] = 'low'
    request = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        messages = {401: 'API 키 인증에 실패했습니다.', 403: '모델 접근 권한 또는 네트워크 정책을 확인하세요.', 429: 'API 사용 한도·잔액·요청 제한을 확인하세요.'}
        raise ValueError(messages.get(exc.code, 'AI 서비스 요청이 실패했습니다. 기존 문항은 유지됩니다.')) from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('AI 서비스에 연결하지 못했거나 시간 제한을 초과했습니다. 기존 문항은 유지됩니다.') from None
    if on_usage:
        on_usage(result.get('usage'))
    if result['choices'][0].get('finish_reason') not in (None, 'stop'):
        raise ValueError('모델 응답이 중단되었습니다. 페이지 수를 줄여 새 시험지로 시도하세요. 사용 비용은 기록했습니다.')
    items = json.loads(result['choices'][0]['message']['content'])['questions']
    if not isinstance(items, list) or len(items) > 200:
        raise ValueError('AI 응답의 문항 목록이 올바르지 않습니다.')
    if any(not isinstance(q,dict) for q in items):
        raise ValueError('AI 응답의 문항 객체 형식을 확인할 수 없습니다.')
    for q in items:q.update(source='ai',status='검토 필요')
    return items
