import io
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import uuid

import fitz
from flask import Flask, abort, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException
from werkzeug.local import LocalProxy
from analysis import difficulty, local_questions, vision_questions


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
    app.config.update(MAX_CONTENT_LENGTH=(4 if cloud else 30) * 1024 * 1024, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=cloud)

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
        if request.method in ('POST', 'PATCH', 'DELETE') and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
            abort(403, '화면을 새로고침한 뒤 다시 시도하세요.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
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

    def validate(q, pages):
        clean = {}
        for name in ['number', 'text', 'unit', 'subunit', 'achievement', 'form', 'kind', 'concepts', 'feature', 'reason', 'warning']:
            value = q.get(name, '')
            if not isinstance(value, str) or len(value) > 30000:
                raise ValueError('문항 텍스트 형식이 올바르지 않습니다.')
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
        qs = local_questions(pages)
        notice = '로컬 예비 인식: AI 분석이 아닙니다. 모든 문항을 원본과 대조하세요.' if qs else '문항을 자동 인식하지 못했습니다. 스캔·이미지는 AI 인식 설정 또는 수동 문항 추가가 필요합니다.'
        with db() as conn:
            conn.execute('INSERT INTO exams(id,metadata,filename,pages,mode,notice) VALUES(?,?,?,?,?,?)', (eid,json.dumps(meta,ensure_ascii=False),'original.'+ext,count,'local',notice))
            for q in qs:
                clean = validate(q,count)
                clean.update(source='local', ai_snapshot=None)
                conn.execute('INSERT INTO questions VALUES(?,?,?)',(uuid.uuid4().hex,eid,json.dumps(clean,ensure_ascii=False)))
        return jsonify(id=eid), 201

    @app.get('/api/exams/<eid>')
    def detail(eid):
        e = exam_record(eid)
        e['metadata'] = json.loads(e['metadata'])
        with db() as conn:
            rows = conn.execute('SELECT * FROM questions WHERE exam_id=? ORDER BY rowid', (eid,)).fetchall()
        e['questions'] = [dict(id=r['id'], **json.loads(r['data'])) for r in rows]
        return jsonify(e)

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
        return send_file(root / eid / e['filename'],as_attachment=True)

    @app.post('/api/exams/<eid>/questions')
    def add_question(eid):
        e = exam_record(eid)
        q = validate(request.get_json(),e['pages'])
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
            incoming = request.get_json()
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

    @app.post('/api/exams/<eid>/analyze')
    def analyze(eid):
        if cloud:
            abort(403, '이번 Vercel 체험 배포에서는 외부 AI 호출을 사용하지 않습니다.')
        e = exam_record(eid)
        if (request.get_json(silent=True) or {}).get('consent') is not True:
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
        items = vision_questions(images,json.loads(e['metadata']))
        if not items:
            raise ValueError('AI가 문항을 찾지 못했습니다. 원본을 확인하고 수동 입력하세요.')
        cleaned = []
        for q in items:
            clean = validate(q,e['pages'])
            clean.update(source='ai',ai_snapshot=dict(clean))
            cleaned.append(clean)
        with db() as conn:
            conn.execute('DELETE FROM questions WHERE exam_id=?',(eid,))
            for q in cleaned:
                conn.execute('INSERT INTO questions VALUES(?,?,?)',(uuid.uuid4().hex,eid,json.dumps(q,ensure_ascii=False)))
            conn.execute("UPDATE exams SET mode='ai',notice=? WHERE id=?",('AI 제안입니다. 교사 검토 후 확정하세요.',eid))
        return jsonify(ok=True)

    return app

if __name__ == '__main__':
    create_app().run(host='127.0.0.1',port=5000,debug=False,threaded=False)
