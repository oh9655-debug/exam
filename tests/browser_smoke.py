"""Optional browser check: run app.py first; pip install playwright; provide Chromium.
Does not call external AI. Creates one synthetic exam in the running app.
"""
from pathlib import Path
import os
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    executable = os.environ.get('CHROMIUM_PATH')
    browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
    page = browser.new_page(viewport={'width':1440,'height':1100})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:5000/settings')
    page.locator('#password').fill('5162')
    page.get_by_role('button',name='관리자 로그인 →').click()
    page.locator('#settings-panel').wait_for(state='visible')
    page.locator('#api-key').fill('sk-fake-browser-test-no-paid-call')
    with page.expect_response(lambda r:'/api/admin/settings' in r.url and r.request.method=='PUT') as saved_prefs:
        page.get_by_role('button',name='설정 저장',exact=True).click()
    assert saved_prefs.value.status==200
    page.wait_for_function("document.querySelector('#key-state').textContent.includes('등록됨')")
    assert page.locator('#api-key').input_value()==''
    page.reload()
    page.locator('#settings-panel').wait_for(state='visible')
    assert '등록됨' in page.locator('#key-state').inner_text()
    assert 'sk-fake' not in page.content()
    page.goto('http://127.0.0.1:5000')
    page.evaluate('document.fonts.ready')
    assert page.evaluate('document.fonts.check("400 14px SCoreDream")')
    page.screenshot(path='/tmp/exam-dashboard-desktop.png',full_page=True)
    page.locator('#start-ai').uncheck()
    page.locator('[name=file]').set_input_files(str(root/'samples/sample-text.pdf'))
    page.locator('[name=school]').fill('브라우저 검증중')
    page.locator('[name=scope]').fill('방정식·기하·함수')
    page.get_by_role('button',name='시험지 등록 및 로컬 인식 →').click()
    page.locator('#question-list button').nth(2).wait_for()
    assert page.locator('#question-list button').count()==3
    assert page.locator('#q-page_end').input_value()=='1'
    page.locator('#question-list button').nth(2).click()
    assert page.locator('#q-page_end').input_value()=='2'
    page.locator('#question-list button').nth(0).click()
    page.locator('#q-form').select_option('단답형')
    page.locator('#q-kind').select_option('계산')
    for i in range(5): page.locator(f'#factor-{i}').select_option('1')
    assert '25.00' in page.locator('#calculated').inner_text()
    page.locator('#q-reason').fill('브라우저 검증: 교사가 단일 개념 기본 적용으로 판정')
    page.locator('#q-status').select_option('교사 검토 완료')
    with page.expect_response(lambda r: r.url.endswith('/api/workspace') and r.request.method=='POST') as saved:
        page.get_by_role('button',name='문항 저장 · 통계 갱신').click()
    assert saved.value.status==200
    page.wait_for_function("document.querySelector('#message').textContent.includes('저장했습니다')")
    page.get_by_role('button',name='수정 이력',exact=True).click()
    page.locator('#history-list details').first.wait_for()
    assert '미평가 → 하' in page.locator('#history-list').inner_text()
    page.get_by_role('button',name='시험지 통계',exact=True).click()
    assert page.locator('#stats-table tr').count()==3
    assert '15.0점' in page.locator('#metrics').inner_text()
    assert '하' in page.locator('#stats-table tr').first.inner_text()
    page.get_by_role('button',name='외부 AI로 인식·분석',exact=True).click()
    page.wait_for_function("document.querySelector('#message').textContent.includes('교사 수정 내용을 보호')")
    assert not page.locator('#consent').is_visible()
    page.get_by_role('button',name='← 시험지 목록').click()
    page.reload()
    page.locator('#search').fill('브라우저 검증중')
    page.locator('#exam-list button').first.click()
    page.locator('#q-reason').wait_for()
    assert '브라우저 검증' in page.locator('#q-reason').input_value()
    # User-provided HTML is text, never executable markup.
    page.locator('#q-text').fill('<img src=x onerror="window.injected=true">')
    page.locator('#q-status').select_option('검토 필요')
    with page.expect_response(lambda r: r.url.endswith('/api/workspace') and r.request.method=='POST'):
        page.get_by_role('button',name='문항 저장 · 통계 갱신').click()
    page.wait_for_function("document.querySelector('#message').textContent.includes('저장했습니다')")
    assert page.evaluate('window.injected === undefined')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert errors==[], errors
    page.screenshot(path='/tmp/exam-mobile.png',full_page=True)
    page.goto('http://127.0.0.1:5000/settings')
    page.locator('#settings-panel').wait_for(state='visible')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.set_viewport_size({'width':1440,'height':1100})
    page.screenshot(path='/tmp/exam-admin-desktop.png',full_page=True)
    browser.close()
    print('Browser workflow passed: upload, multipage, edit, history, statistics, consent cancellation, reopen, text escaping, mobile layout.')
