"""Browser-scoped encrypted settings; no API key is returned by any endpoint."""
import base64
import hashlib
import json
import math
import secrets
import sqlite3
import time
from functools import wraps
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import Blueprint, abort, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_MODELS = [
    dict(id='gpt-4.1-mini', name='GPT-4.1 mini · 추천', input=0.40, cached=0.10, output=1.60, note='이미지 인식·일반 분석의 균형. 추론 토큰 없이 비용 예측이 쉬움.'),
    dict(id='gpt-5-mini', name='GPT-5 mini', input=0.25, cached=0.025, output=2.00, note='복합 추론 비교용. 추론 토큰도 출력 요금에 포함.'),
    dict(id='gpt-4.1', name='GPT-4.1 · 정밀 검토', input=2.00, cached=0.50, output=8.00, note='어려운 문항을 비교 검토할 때. mini보다 비용이 높음.'),
]
COOKIE = 'exam_preferences'


def require_admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if session.get('admin_until', 0) <= time.time():
            abort(401, '설정에서 관리자 비밀번호로 먼저 로그인하세요.')
        return fn(*args, **kwargs)
    return wrapped


def selected_model():
    return next(m for m in g.preferences['models'] if m['id'] == g.preferences['model'])


def usage_cost(usage, model):
    incoming = max(0, int(usage.get('prompt_tokens', 0)))
    outgoing = max(0, int(usage.get('completion_tokens', 0)))
    cached = min(incoming, max(0, int(usage.get('prompt_tokens_details', {}).get('cached_tokens', 0))))
    return round(((incoming-cached)*model['input'] + cached*model['cached'] + outgoing*model['output'])/1_000_000, 8)


def estimate(pages, model, text_tokens=2500):
    # Conservative image allowance; actual provider tokenization can differ.
    image_tokens = 4000 if model['id'].startswith(('gpt-4.1', 'gpt-5')) else 20000
    inputs = pages*image_tokens + text_tokens
    output = 12000
    return dict(input_tokens=inputs, output_tokens=output,
                estimated_usd=round((inputs*model['input'] + output*model['output'])/1_000_000, 6),
                max_output_tokens=output, model=model['id'], pages=pages,
                notice='이미지·응답 길이에 따른 참고 예상치이며 청구액이나 상한 보장이 아닙니다. 실제 사용 토큰으로 다시 계산합니다.')


def record_usage(eid, model, usage=None):
    ledger = dict(session.get('billing', dict(count=0, usd=0.0, input=0, output=0, unknown=0, recent=[])))
    cost = usage_cost(usage, model) if usage is not None else None
    item = dict(id=secrets.token_hex(4), exam=eid[:8], model=model['id'], at=int(time.time()),
                usd=cost, input=int((usage or {}).get('prompt_tokens', 0)), output=int((usage or {}).get('completion_tokens', 0)),
                status='응답 수신 · 결과 검증 중', rates={k:model[k] for k in ['input','cached','output']})
    ledger['count'] += 1
    if cost is None:
        ledger['unknown'] += 1
    else:
        ledger['usd'] = round(ledger['usd'] + cost, 8)
    ledger['input'] += item['input']; ledger['output'] += item['output']
    ledger['recent'] = [item] + ledger['recent'][:7]
    session['billing'] = ledger
    return item['id']


def finish_usage(uid, success):
    ledger = dict(session.get('billing', {}))
    for row in ledger.get('recent', []):
        if row['id'] == uid:
            row['status'] = '분석 완료' if success else '분석 실패 · 과금 가능'
    session['billing'] = ledger


def init_settings(app, base, cloud):
    cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256((app.secret_key+'|api-settings-v1').encode()).digest()))
    bp = Blueprint('settings', __name__)

    @app.before_request
    def load_preferences():
        g.preferences = dict(key='', model='gpt-4.1-mini', models=[dict(m) for m in DEFAULT_MODELS])
        token = request.cookies.get(COOKIE)
        if token:
            try:
                g.preferences = json.loads(cipher.decrypt(token.encode(), ttl=180*86400))
            except (InvalidToken, ValueError, TypeError):
                pass
        g.preferences_changed = False

    @app.after_request
    def save_preferences(response):
        if getattr(g, 'preferences_changed', False):
            token = cipher.encrypt(json.dumps(g.preferences, separators=(',',':'),ensure_ascii=False).encode()).decode()
            response.set_cookie(COOKIE, token, max_age=180*86400, secure=cloud, httponly=True, samesite='Strict')
        return response

    @bp.post('/api/admin/login')
    def login():
        # Shared instance-side rate limiter, not reset by clearing browser cookies.
        address = hashlib.sha256(request.remote_addr.encode()).hexdigest()
        path = Path(base)/'login-rate.sqlite3'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS attempts(address TEXT PRIMARY KEY, count INTEGER, since REAL)')
            conn.execute('DELETE FROM attempts WHERE since < ?', (time.time()-900,))
            row = conn.execute('SELECT count FROM attempts WHERE address=?', (address,)).fetchone()
            if row and row[0] >= 5:
                abort(429, '로그인 시도가 많습니다. 15분 뒤 다시 시도하세요.')
            password = str((request.get_json(silent=True) or {}).get('password', ''))
            stored = g.preferences.get('password_hash')
            valid = check_password_hash(stored, password) if stored else secrets.compare_digest(password, '5162')
            if not valid:
                conn.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(address) DO UPDATE SET count=count+1', (address,time.time()))
                # abort outside transaction so the counter is committed.
            else:
                conn.execute('DELETE FROM attempts WHERE address=?', (address,))
        if not valid:
            abort(401, '비밀번호가 일치하지 않습니다.')
        session.permanent = True
        session['admin_until'] = time.time()+1800
        return jsonify(ok=True)

    @bp.post('/api/admin/logout')
    def logout():
        session.pop('admin_until', None)
        return jsonify(ok=True)

    @bp.get('/api/admin/settings')
    @require_admin
    def preferences():
        return jsonify(model=g.preferences['model'], models=g.preferences['models'],
                       key_configured=bool(g.preferences.get('key')), default_password=not bool(g.preferences.get('password_hash')),
                       billing=session.get('billing', dict(count=0,usd=0,input=0,output=0,unknown=0,recent=[])))

    @bp.put('/api/admin/settings')
    @require_admin
    def update():
        data = request.get_json()
        candidate = dict(g.preferences)
        models = data.get('models', candidate['models'])
        if not isinstance(models, list) or not 1 <= len(models) <= 5:
            raise ValueError('모델은 1~5개까지 등록할 수 있습니다.')
        ids = set()
        for m in models:
            import re
            if not isinstance(m, dict) or not isinstance(m.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9._:-]{1,80}', m.get('id','')) or m['id'] in ids:
                raise ValueError('서로 다른 올바른 모델 ID를 입력하세요.')
            ids.add(m['id'])
            if not isinstance(m.get('name'),str) or not 1 <= len(m['name']) <= 50:
                raise ValueError('모델 표시 이름을 확인하세요.')
            m['note'] = str(m.get('note',''))[:120]
            for k in ['input','cached','output']:
                v=m.get(k)
                if type(v) not in (float,int) or not math.isfinite(v) or not 0 <= v <= 1000:
                    raise ValueError('단가는 100만 토큰당 USD로 입력하세요.')
        candidate['models'] = models
        candidate['model'] = data.get('model',candidate['model'])
        if candidate['model'] not in ids: raise ValueError('등록된 모델을 선택하세요.')
        key = data.get('key','').strip()
        if key:
            if len(key)>400 or not key.startswith('sk-'): raise ValueError('OpenAI API 키 형식을 확인하세요.')
            candidate['key']=key
        if data.get('delete_key') is True: candidate['key']=''
        password=data.get('new_password','')
        if password:
            if not 8 <= len(password) <= 80: raise ValueError('새 비밀번호는 8~80자로 입력하세요.')
            candidate['password_hash']=generate_password_hash(password)
        size=len(cipher.encrypt(json.dumps(candidate,ensure_ascii=False,separators=(',',':')).encode()))
        if size>3400: raise ValueError('설정 용량을 초과했습니다. 모델 이름과 설명을 줄이세요.')
        g.preferences=candidate;g.preferences_changed=True
        return jsonify(ok=True,key_configured=bool(candidate['key']))

    @bp.get('/api/ai/status')
    def status():
        return jsonify(configured=bool(g.preferences.get('key')), model=selected_model()['name'],
                       admin=session.get('admin_until',0)>time.time())

    app.register_blueprint(bp)
