import io
from datetime import timedelta
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import secrets
import time
import sqlite3
import uuid

import fitz
from flask import Flask, abort, jsonify, render_template, request, send_file, session, g, make_response
from werkzeug.exceptions import HTTPException
from werkzeug.local import LocalProxy
from analysis import difficulty, local_questions, vision_questions, normalize_ai_question
from workspace import export_exam, restore_exam
from settings import init_settings, require_admin, selected_model, estimate, record_usage, finish_usage


def create_app(data_dir=None):
    app = Flask(__name__)
    cloud = os.environ.get('VERCEL') == '1'
    base = Path(data_dir or os.environ.get('EXAM_DATA_DIR', '/tmp/exam-mvp' if cloud else Path(__file__).parent / 'data')).resolve()
    base.mkdir(parents=True, exist_ok=True)
    if cloud:
        secret = os.environ.get('EXAM_SESSION_SECRET', '')
        if len(secret) < 32:
            raise RuntimeError('Vercel 환경 변수 EXAM_SESSION_SECRET에 32자 이상의 무작위 값을 설정하세요.')
        app.secret_key = secret
        root = LocalProxy(lambda: base / session['workspace'])
    else:
        root = base
        secret_file = root / '.session-key'
        if not secret_file.exists():
            secret_file.write_text(secrets.token_hex(32))
        app.secret_key = secret_file.read_text()
    app.config.update(MAX_FORM_MEMORY_SIZE=800000, MAX_CONTENT_LENGTH=(4 if cloud else 30) * 1024 * 1024, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=cloud, PERMANENT_SESSION_LIFETIME=timedelta(days=180), SESSION_REFRESH_EACH_REQUEST=False)

    @contextmanager
    def db():
        conn = sqlite3.connect(str(root / 'exam.sqlite3'))
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize():
      root.mkdir(parents=True, exist_ok=True)
      with db() as conn:
        conn.executescript('''CREATE TABLE IF NOT EXISTS exams(id TEXT PRIMARY KEY, metadata TEXT NOT NULL, filename TEXT NOT NULL, pages INTEGER NOT NULL, mode TEXT NOT NULL, notice TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY, exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE, before_data TEXT NOT NULL, after_data TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);''')

    if not cloud:
        initialize()

    @app.before_request
    def security():
        if not cloud and request.host.split(':')[0] not in ('127.0.0.1', 'localhost', '[::1]'):
            abort(403, '로컬 PC에서만 접근할 수 있습니다.')
        if cloud:
            if 'workspace' not in session:
                session['workspace'] = uuid.uuid4().hex
            initialize()
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(24)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
            abort(403, '화면을 새로고침한 뒤 다시 시도하세요.')

    init_settings(app, base, cloud)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HTTPException):
            description = '시험지 자료를 찾지 못했습니다. 브라우저 보관본이 없는 이전 자료는 원본 파일로 다시 등록해 주세요.' if exc.code==404 else exc.description
            return jsonify(error=description), exc.code
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        app.logger.exception('Operation failed')
        return jsonify(error='처리에 실패했습니다. 파일 형식과 API 설정을 확인하세요. 저장된 원본은 보존됩니다.'), 500

    def exam_record(eid):
        with db() as conn:
            row = conn.execute('SELECT * FROM exams WHERE id=?', (eid,)).fetchone()
        if not row:
            abort(404)
        return dict(row)

    def input_data():
        return g.workspace_payload if hasattr(g,'workspace_payload') else request.get_json()

    def validate(q, pages):
        clean = {}
        for name in ['number', 'text', 'unit', 'subunit', 'achievement', 'form', 'kind', 'concepts', 'feature', 'reason', 'warning']:
            value = q.get(name, '')
            if not isinstance(value, str) or len(value) > 30000:
                raise ValueError(f'문항 {q.get("number", "미확인")}의 {name} 항목이 문자열이 아니거나 길이 제한을 넘었습니다.')
            clean[name] = value
        if not clean['number'].strip() or not clean['text'].strip():
            raise ValueError('문항 번호와 원문을 입력하세요.')
        for name in ['page_start', 'page_end']:
            value = q.get(name, 1)
            if type(value) is not int or not 1 <= value <= pages:
                raise ValueError('원본 페이지 범위를 확인하세요.')
            clean[name] = value
        if clean['page_end'] < clean['page_start']:
            raise ValueError('끝 페이지는 시작 페이지 이후여야 합니다.')
        points = q.get('points')
        if points is not None and (type(points) not in (int, float) or not math.isfinite(points) or not 0 <= points <= 100):
            raise ValueError('배점은 0~100 숫자 또는 미확인이어야 합니다.')
        clean['points'] = points
        factors = q.get('factors')
        score, level = difficulty(factors) if factors is not None else (None, None)
        override = q.get('override_level') or None
        if override is not None and override not in ['최하', '하', '중', '상', '최상']:
            raise ValueError('난이도 선택이 올바르지 않습니다.')
        clean.update(factors=factors, score=score, level=level, override_level=override)
        status = q.get('status', '검토 필요')
        if status not in ['검토 필요', '교사 검토 완료']:
            raise ValueError('검토 상태가 올바르지 않습니다.')
        if status == '교사 검토 완료' and (not (override or level) or points is None or clean['unit'] in ('', '미분류') or clean['form'] not in ['객관식', '단답형', '서술형'] or clean['kind'] in ('', '미분류')):
            raise ValueError('검토 완료 전에 단원·문제 형태·유형·배점·난이도를 확인하세요.')
        clean['status'] = status
        return clean

    @app.get('/')
    def index():
        return render_template('index.html', csrf=session['csrf'], cloud=cloud)

    @app.get('/settings')
    def settings_page():
        return render_template('settings.html',csrf=session['csrf'])

    @app.get('/api/exams')
    def exams():
        with db() as conn:
            rows = conn.execute('SELECT * FROM exams ORDER BY created DESC, rowid DESC').fetchall()
        return jsonify([dict(id=r['id'], metadata=json.loads(r['metadata']), mode=r['mode'], created=r['created']) for r in rows])

    @app.post('/api/exams')
    def upload():
        file = request.files.get('file')
        if not file:
            raise ValueError('PDF, JPG 또는 PNG 파일을 선택하세요.')
        meta = {k: request.form.get(k, '').strip() for k in ['school', 'grade', 'year', 'semester', 'exam_type', 'scope', 'curriculum']}
        if not meta['school'] or meta['grade'] not in ['1', '2', '3'] or not meta['year'].isdigit() or not 1900 <= int(meta['year']) <= 2100 or meta['semester'] not in ['1', '2']:
            raise ValueError('학교·학년·연도·학기를 확인하세요.')
        blob = file.read()
        # Determine content by magic; extension alone is not trusted.
        ext = 'pdf' if blob.startswith(b'%PDF-') else 'png' if blob.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if blob.startswith(b'\xff\xd8\xff') else None
        if ext is None:
            raise ValueError('지원하는 실제 PDF/JPG/PNG 파일이 아닙니다.')
        try:
            document = fitz.open(stream=blob, filetype=ext)
            if document.needs_pass or not 1 <= len(document) <= 30:
                raise ValueError('암호 없는 1~30페이지 파일을 사용하세요.')
            if ext != 'pdf':
                pdf = document.convert_to_pdf()
                document.close()
                document = fitz.open(stream=pdf, filetype='pdf')
            pages = [p.get_text(sort=True) for p in document]
            eid = uuid.uuid4().hex
            folder = root / eid
            folder.mkdir()
            (folder / ('original.' + ext)).write_bytes(blob)
            document.save(folder / 'preview.pdf')
            count = len(document)
            document.close()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('손상되었거나 읽을 수 없는 파일입니다.') from exc
        optimized = request.form.get('optimized') == '1'
        if optimized:
            original_name = request.form.get('original_name', '')[:200]
            try:
                original_size = int(request.form.get('original_size', '0'))
            except ValueError:
                raise ValueError('원본 파일 정보를 확인하세요.')
            if not 0 < original_size <= 100*1024*1024:
                raise ValueError('원본은 100MB 이하 파일을 사용하세요.')
            meta.update(optimized=True,original_name=original_name,original_size=original_size)
            raw_text = request.form.get('source_text')
            if raw_text:
                if len(raw_text.encode('utf-8')) > 250000:
                    raise ValueError('텍스트 추출 결과가 너무 큽니다.')
                try:
                    source_pages = json.loads(raw_text)
                except ValueError:
                    raise ValueError('PDF 텍스트 정보를 읽을 수 없습니다.')
                if not isinstance(source_pages,list) or len(source_pages)!=count or any(not isinstance(x,str) or len(x)>10000 for x in source_pages):
                    raise ValueError('PDF 텍스트의 페이지 정보를 확인하세요.')
                pages = source_pages
        qs = local_questions(pages)
        notice = '로컬 예비 인식: AI 분석이 아닙니다. 모든 문항을 원본과 대조하세요.' if qs else '문항을 자동 인식하지 못했습니다. 스캔·이미지는 AI 인식 설정 또는 수동 문항 추가가 필요합니다.'
        if optimized:
            notice += ' 큰 파일의 업로드용 사본입니다. 변경 없는 원본은 등록한 브라우저에만 보관됩니다. AI는 사본을 사용하므로 작은 수식·도형을 원본과 대조하세요.'
        with db() as conn:
            conn.execute('INSERT INTO exams(id,metadata,filename,pages,mode,notice) VALUES(?,?,?,?,?,?)', (eid,json.dumps(meta,ensure_ascii=False),'original.'+ext,count,'local',notice))
            for q in qs:
                clean = validate(q,count)
                clean.update(source='local', ai_snapshot=None)
                conn.execute('INSERT INTO questions VALUES(?,?,?)',(uuid.uuid4().hex,eid,json.dumps(clean,ensure_ascii=False)))
        return jsonify(id=eid,exam=export_exam(eid,db,exam_record)), 201

    @app.get('/api/exams/<eid>')
    def detail(eid):
        return jsonify(export_exam(eid,db,exam_record))

    @app.get('/api/exams/<eid>/pages/<int:page>.png')
    def page_image(eid,page):
        e = exam_record(eid)
        if not 1 <= page <= e['pages']:
            abort(404)
        with fitz.open(root / eid / 'preview.pdf') as doc:
            p = doc[page-1]
            scale = min(1.5, 2000/max(p.rect.width,p.rect.height))
            blob = p.get_pixmap(matrix=fitz.Matrix(scale,scale)).tobytes('png')
        return send_file(io.BytesIO(blob),mimetype='image/png')

    @app.get('/api/exams/<eid>/original')
    def original(eid):
        e = exam_record(eid)
        return send_file(root / eid / e['filename'],as_attachment=True,download_name=('upload-copy'+Path(e['filename']).suffix) if json.loads(e['metadata']).get('optimized') else e['filename'])

    @app.post('/api/exams/<eid>/questions')
    def add_question(eid):
        e = exam_record(eid)
        q = validate(input_data(),e['pages'])
        q.update(source='teacher',ai_snapshot=None)
        qid = uuid.uuid4().hex
        with db() as conn:
            conn.execute('INSERT INTO questions VALUES(?,?,?)',(qid,eid,json.dumps(q,ensure_ascii=False)))
        return jsonify(id=qid),201

    @app.patch('/api/questions/<qid>')
    def update_question(qid):
        with db() as conn:
            row = conn.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
            if not row:
                abort(404)
            old = json.loads(row['data'])
            e = exam_record(row['exam_id'])
            incoming = input_data()
            q = validate({**old, **incoming},e['pages'])
            q.update(source=old['source'],ai_snapshot=old.get('ai_snapshot'))
            value = json.dumps(q,ensure_ascii=False)
            conn.execute('INSERT INTO revisions(question_id,before_data,after_data) VALUES(?,?,?)',(qid,row['data'],value))
            conn.execute('UPDATE questions SET data=? WHERE id=?',(value,qid))
        return jsonify(ok=True)

    @app.delete('/api/questions/<qid>')
    def delete_question(qid):
        with db() as conn:
            result = conn.execute('DELETE FROM questions WHERE id=?', (qid,))
            if not result.rowcount:
                abort(404)
        return jsonify(ok=True)

    @app.get('/api/questions/<qid>/history')
    def history(qid):
        with db() as conn:
            rows = conn.execute('SELECT * FROM revisions WHERE question_id=? ORDER BY id DESC',(qid,)).fetchall()
        return jsonify([dict(created=r['created'],before=json.loads(r['before_data']),after=json.loads(r['after_data'])) for r in rows])

    @app.get('/api/exams/<eid>/estimate')
    @require_admin
    def analysis_estimate(eid):
        e = exam_record(eid)
        return jsonify(**estimate(e['pages'],selected_model()),key_configured=bool(g.preferences.get('key')))

    @app.post('/api/exams/<eid>/analyze')
    def analyze(eid):
        managed = bool(g.preferences.get('key'))
        if cloud or managed:
            if not managed:
                abort(403, '관리자 설정에서 API 키를 등록하세요.')
            if session.get('admin_until',0) <= time.time():
                abort(401, '관리자 로그인 후 분석할 수 있습니다.')
        e = exam_record(eid)
        if (input_data() or {}).get('consent') is not True:
            raise ValueError('외부 전송 및 유료 API 사용 동의가 필요합니다.')
        # Keep this intentionally append-free: existing teacher work cannot be overwritten.
        with db() as conn:
            existing = conn.execute('SELECT data FROM questions WHERE exam_id=?',(eid,)).fetchall()
        if any(json.loads(r['data'])['source'] != 'local' or json.loads(r['data'])['status'] == '교사 검토 완료' for r in existing):
            raise ValueError('수정한 문항을 보호하기 위해 재분석을 중단했습니다. 원본을 새 시험지로 등록하세요.')
        with db() as conn:
            edited = conn.execute('SELECT 1 FROM revisions r JOIN questions q ON q.id=r.question_id WHERE q.exam_id=? LIMIT 1',(eid,)).fetchone()
        if edited:
            raise ValueError('수정 이력이 있어 덮어쓰지 않습니다. 원본을 새 시험지로 등록하세요.')
        if e['pages'] > 10:
            raise ValueError('MVP AI 인식은 비용·처리량 제한을 위해 최대 10페이지입니다.')
        with fitz.open(root / eid / 'preview.pdf') as doc:
            images = [p.get_pixmap(matrix=fitz.Matrix(min(1.5,2000/max(p.rect.width,p.rect.height)),min(1.5,2000/max(p.rect.width,p.rect.height)))).tobytes('png') for p in doc]
        usage_id = None
        def received_usage(usage):
            nonlocal usage_id
            usage_id = record_usage(eid, selected_model(), usage)
        try:
            if managed:
                items = vision_questions(images,json.loads(e['metadata']),key=g.preferences['key'],model=selected_model()['id'],on_usage=received_usage)
            else:
                items = vision_questions(images,json.loads(e['metadata']))
        except Exception:
            if managed:
                if usage_id is None: usage_id = record_usage(eid,selected_model())
                finish_usage(usage_id,False)
            raise
        try:
            store_ai_items(eid,e,items)
            if usage_id: finish_usage(usage_id,True)
            return jsonify(ok=True,billing=session.get('billing',{}))
        except ValueError as error:
            if usage_id: finish_usage(usage_id,False)
            return jsonify(error=str(error),analysis_draft={'questions':items,'usage_id':usage_id},billing=session.get('billing',{})),400
        except Exception:
            if usage_id: finish_usage(usage_id,False)
            raise

    def store_ai_items(eid,e,items):
        if not isinstance(items,list) or not 1<=len(items)<=200:
            raise ValueError('AI가 문항 목록을 반환하지 못했습니다.')
        cleaned=[]
        for raw in items:
            q=normalize_ai_question(raw)
            clean=validate(q,e['pages'])
            snapshot=dict(clean)
            if '_raw_response' in q:snapshot['raw_response']=q['_raw_response']
            clean.update(source='ai',ai_snapshot=snapshot)
            cleaned.append(clean)
        with db() as conn:
            conn.execute('DELETE FROM questions WHERE exam_id=?',(eid,))
            for q in cleaned:
                conn.execute('INSERT INTO questions VALUES(?,?,?)',(uuid.uuid4().hex,eid,json.dumps(q,ensure_ascii=False)))
            conn.execute("UPDATE exams SET mode='ai',notice=? WHERE id=?",('AI 분석 제안입니다. 원본·배점·난이도 근거를 교사가 확인하세요.',eid))

    @app.post('/api/exams/<eid>/recover')
    def recover_analysis(eid):
        e=exam_record(eid)
        with db() as conn:
            rows=conn.execute('SELECT data FROM questions WHERE exam_id=?',(eid,)).fetchall()
            edited=conn.execute('SELECT 1 FROM revisions r JOIN questions q ON q.id=r.question_id WHERE q.exam_id=? LIMIT 1',(eid,)).fetchone()
        if edited or any(json.loads(row['data'])['source']!='local' for row in rows):
            raise ValueError('수정한 문항을 덮어쓰지 않습니다. 별도 시험지로 등록해 주세요.')
        payload=input_data() or {}
        store_ai_items(eid,e,payload.get('questions'))
        uid=payload.get('usage_id')
        if uid and any(row.get('id')==uid and row.get('exam')==eid[:8] for row in session.get('billing',{}).get('recent',[])):
            finish_usage(uid,True)
        return jsonify(ok=True,recovered=True)

    @app.post('/api/workspace')
    def portable_workspace():
        allowed={'detail','page_image','original','add_question','update_question','delete_question','history','analysis_estimate','analyze','recover_analysis'}
        target=request.form.get('path','')
        method=request.form.get('method','GET').upper()
        if method not in ('GET','POST','PATCH','DELETE'):
            abort(400,'작업 형식을 확인하세요.')
        try:
            endpoint,values=app.url_map.bind('').match(target,method=method)
        except HTTPException:
            abort(400,'지원하지 않는 시험지 작업입니다.')
        if endpoint not in allowed: abort(400,'지원하지 않는 시험지 작업입니다.')
        file=request.files.get('file')
        if not file: raise ValueError('브라우저에 보관된 사본을 찾지 못했습니다. 원본으로 다시 등록해 주세요.')
        eid=restore_exam(request.form.get('state'),file.read(),root,db,validate)
        if values.get('eid',eid)!=eid: abort(400,'시험지가 일치하지 않습니다.')
        if 'qid' in values:
            with db() as conn:
                q=conn.execute('SELECT 1 FROM questions WHERE id=? AND exam_id=?',(values['qid'],eid)).fetchone()
            if not q: abort(404)
        try: g.workspace_payload=json.loads(request.form.get('payload','null'))
        except ValueError: raise ValueError('문항 변경 내용을 읽지 못했습니다.')
        result=make_response(app.view_functions[endpoint](**values))
        if result.status_code>=400 or not result.is_json: return result
        return jsonify(result=result.get_json(),exam=export_exam(eid,db,exam_record)),result.status_code

    return app

if __name__ == '__main__':
    create_app().run(host='127.0.0.1',port=5000,debug=False,threaded=False)
