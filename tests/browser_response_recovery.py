"""Legacy malformed response is preserved and recovered without a second API call.
Uses rotating_server.py synthetic AI only; never calls a paid service.
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'))
    page=browser.new_page();calls=[]
    def legacy_failure(route):
        if b'/analyze' not in (route.request.post_data_buffer or b''):
            route.continue_();return
        calls.append(1)
        response=route.fetch();data=response.json()
        assert response.status==200
        draft=[q['ai_snapshot'].get('raw_response',q) for q in data['exam']['questions']]
        usage_id=data['result']['billing']['recent'][0]['id']
        route.fulfill(response=response,status=400,json={'error':'응답 형식 검사 실패 (회귀 검증용)','analysis_draft':{'questions':draft,'usage_id':usage_id}})
    page.goto('http://127.0.0.1:5000/settings')
    page.locator('#password').fill('5162');page.get_by_role('button',name='관리자 로그인 →').click()
    page.locator('#settings-panel').wait_for(state='visible')
    page.locator('#api-key').fill('sk-fake-response-recovery')
    page.get_by_role('button',name='설정 저장',exact=True).click()
    page.wait_for_function("document.querySelector('#key-state').textContent.includes('등록됨')")
    page.goto('http://127.0.0.1:5000');page.route('**/api/workspace',legacy_failure)
    page.locator('[name=file]').set_input_files(str(root/'samples/sample-scan.pdf'))
    page.locator('[name=school]').fill('응답 복구 검증')
    page.get_by_role('button',name='시험지 등록 및 로컬 인식 →').click()
    page.locator('#consent').wait_for(state='visible')
    page.get_by_role('button',name='전송 및 유료 API 호출 승인').click()
    page.wait_for_function("document.querySelector('#recover-ai').hidden===false && document.querySelector('#task-status').dataset.state==='error'")
    assert '브라우저에 보관' in page.locator('#task-detail').inner_text()
    page.reload();page.locator('#exam-list button').first.click()
    page.locator('#recover-ai').wait_for(state='visible')
    page.locator('#ai').click()
    page.wait_for_function("document.querySelector('#task-title').textContent.includes('보관된 분석 응답')")
    assert not page.locator('#consent').is_visible()
    assert len(calls)==1
    page.locator('#task-dismiss').click()
    page.locator('#recover-ai').click()
    page.wait_for_function("document.querySelector('#source').textContent.includes('AI 제안')")
    assert page.locator('#question-list button').count()==1
    assert '일차방정식' in page.locator('#q-concepts').input_value()
    assert not page.locator('#recover-ai').is_visible()
    assert len(calls)==1
    page.goto('http://127.0.0.1:5000/settings')
    page.locator('#settings-panel').wait_for(state='visible')
    assert '1회' in page.locator('#usage-metrics').inner_text()
    browser.close()
    print('Response recovery passed: raw numeric/list/null response saved, recovered through a new empty server, one modeled AI call and unchanged cost count.')
