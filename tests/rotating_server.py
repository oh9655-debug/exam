"""Regression server: every HTTP request gets a new, empty Vercel-like filesystem.
Only synthetic AI responses are returned. Never calls a paid API.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
from werkzeug.serving import run_simple
import app as application

os.environ['VERCEL']='1'
os.environ['EXAM_SESSION_SECRET']='synthetic-test-server-session-secret-123456789'

def fake_vision(images,metadata,**kwargs):
    if kwargs.get('on_usage'):kwargs['on_usage']({'prompt_tokens':100,'completion_tokens':200})
    return [dict(number='1',text='모형 AI 문항',page_start=1,page_end=1,points=5,unit='문자와 식',subunit='',achievement='',form='단답형',kind='계산',concepts='일차방정식',feature='기본형',factors=[1,1,1,1,1],reason='실제 과금 없는 회귀 검증용 AI 응답',warning='모형 응답',status='검토 필요')]

application.vision_questions=fake_vision

def fresh(environ,start_response):
    with tempfile.TemporaryDirectory(prefix='exam-fresh-request-') as root:
        app=application.create_app(root)
        return app(environ,start_response)

if __name__=='__main__':
    run_simple('127.0.0.1',5000,fresh,threaded=False)
