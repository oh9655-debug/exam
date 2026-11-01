"""Portable browser-owned exam snapshots. Vercel /tmp is only a request cache."""
import json
import re
from pathlib import Path
import fitz
from flask import abort

ID = re.compile(r'^[0-9a-f]{32}$')
MAX_STATE = 700000


def export_exam(eid, db, exam_record):
    e=exam_record(eid)
    e['metadata']=json.loads(e['metadata'])
    with db() as conn:
        rows=conn.execute('SELECT * FROM questions WHERE exam_id=? ORDER BY rowid',(eid,)).fetchall()
        questions=[]
        for row in rows:
            history=conn.execute('SELECT * FROM revisions WHERE question_id=? ORDER BY id DESC',(row['id'],)).fetchall()
            questions.append(dict(id=row['id'],**json.loads(row['data']),history=[dict(created=h['created'],before=json.loads(h['before_data']),after=json.loads(h['after_data'])) for h in history]))
    e['questions']=questions
    return e


def restore_exam(raw, blob, root, db, validate):
    if not raw or len(raw.encode('utf-8'))>MAX_STATE:
        raise ValueError('보관된 문항·수정 이력의 크기가 전송 한도를 넘었습니다. 원본을 별도로 보관해 주세요.')
    try: state=json.loads(raw)
    except ValueError: raise ValueError('브라우저 보관 자료를 읽지 못했습니다.')
    if not isinstance(state,dict) or not isinstance(state.get('id'),str) or not ID.fullmatch(state['id']):
        raise ValueError('시험지 식별자가 올바르지 않습니다.')
    eid=state['id']
    if not blob or len(blob)>3*1024*1024:
        raise ValueError('보관된 업로드용 사본을 다시 준비해 주세요.')
    ext='pdf' if blob.startswith(b'%PDF-') else 'png' if blob.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if blob.startswith(b'\xff\xd8\xff') else None
    if not ext: raise ValueError('보관 파일 형식을 확인하세요.')
    try:
        doc=fitz.open(stream=blob,filetype=ext)
        if doc.needs_pass or not 1<=len(doc)<=30: raise ValueError()
        if ext!='pdf':
            pdf=doc.convert_to_pdf();doc.close();doc=fitz.open(stream=pdf,filetype='pdf')
        pages=len(doc)
    except Exception: raise ValueError('보관된 시험지 사본을 읽을 수 없습니다. 원본으로 다시 등록해 주세요.') from None
    try:
        metadata=state.get('metadata')
        if not isinstance(metadata,dict): raise ValueError('시험지 기본 정보를 확인하세요.')
        questions=state.get('questions')
        if not isinstance(questions,list) or len(questions)>200: raise ValueError('문항 목록을 확인하세요.')
        cleaned=[];ids=set()
        for q in questions:
            if not isinstance(q,dict) or not isinstance(q.get('id'),str) or not ID.fullmatch(q['id']) or q['id'] in ids:
                raise ValueError('문항 식별자를 확인하세요.')
            ids.add(q['id'])
            value=validate(q,pages)
            value.update(source=q.get('source','teacher'),ai_snapshot=q.get('ai_snapshot'))
            history=q.get('history',[])
            if not isinstance(history,list) or len(history)>1000: raise ValueError('수정 이력 형식을 확인하세요.')
            for h in history:
                if not isinstance(h,dict) or not isinstance(h.get('before'),dict) or not isinstance(h.get('after'),dict):
                    raise ValueError('수정 이력을 읽을 수 없습니다.')
            cleaned.append((q['id'],value,history))
        folder=Path(root)/eid;folder.mkdir(exist_ok=True)
        (folder/('original.'+ext)).write_bytes(blob)
        # Save from a memory document; a previous request may already have a preview.
        (folder/'preview.pdf').write_bytes(doc.tobytes())
        with db() as conn:
            conn.execute('DELETE FROM exams WHERE id=?',(eid,))
            conn.execute('INSERT INTO exams(id,metadata,filename,pages,mode,notice,created) VALUES(?,?,?,?,?,?,?)',
                         (eid,json.dumps(metadata,ensure_ascii=False),'original.'+ext,pages,state.get('mode','local'),str(state.get('notice','')),str(state.get('created',''))))
            for qid,q,history in cleaned:
                conn.execute('INSERT INTO questions VALUES(?,?,?)',(qid,eid,json.dumps(q,ensure_ascii=False)))
                for h in reversed(history):
                    conn.execute('INSERT INTO revisions(question_id,before_data,after_data,created) VALUES(?,?,?,?)',
                                 (qid,json.dumps(h['before'],ensure_ascii=False),json.dumps(h['after'],ensure_ascii=False),str(h.get('created',''))[:40]))
        return eid
    finally:doc.close()
