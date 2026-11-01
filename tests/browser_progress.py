"""Progress, slow network, timeout and cancellation UI. Use rotating_server.py; no paid calls."""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    root=Path(__file__).resolve().parents[1]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'))
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:5000/settings')
        await page.locator('#password').fill('5162');await page.get_by_role('button',name='관리자 로그인 →').click()
        await page.locator('#settings-panel').wait_for(state='visible')
        await page.locator('#api-key').fill('sk-fake-progress-test')
        await page.get_by_role('button',name='설정 저장',exact=True).click()
        await page.wait_for_function("document.querySelector('#key-state').textContent.includes('등록됨')")
        await page.goto('http://127.0.0.1:5000')
        await page.locator('#start-ai').uncheck()
        await page.locator('[name=file]').set_input_files(str(root/'samples/sample-scan.pdf'))
        await page.locator('[name=school]').fill('진행 상태 검증')
        await page.get_by_role('button',name='시험지 등록 및 로컬 인식 →').click()
        await page.wait_for_function("document.querySelector('#workspace').hidden===false && document.querySelector('#ai').disabled===false")
        async def delayed_status(route):
            await asyncio.sleep(1.5);await route.continue_()
        async def delayed_estimate(route):
            assert len(route.request.post_data_buffer)<100 # no exam PDF for estimate
            await asyncio.sleep(1);await route.continue_()
        await page.route('**/api/ai/status',delayed_status)
        await page.route('**/api/ai/estimate',delayed_estimate)
        await page.locator('#start-scan-ai').click()
        await page.wait_for_function("document.querySelector('#task-title').textContent.includes('API 키')")
        assert await page.locator('#ai').is_disabled() and await page.locator('#start-scan-ai').is_disabled()
        await page.wait_for_function("document.querySelector('#task-elapsed').textContent!=='00:00'")
        await page.locator('#consent').wait_for(state='visible')
        assert '승인 대기' in await page.locator('#task-title').inner_text()
        await page.get_by_role('button',name='취소',exact=True).click()
        assert '시작하지 않았습니다' in await page.locator('#task-title').inner_text()
        assert await page.locator('#ai').is_enabled()
        async def delayed_analysis(route):
            await asyncio.sleep(2);await route.continue_()
        await page.route('**/api/workspace',delayed_analysis)
        await page.locator('#ai').click();await page.locator('#consent').wait_for(state='visible')
        await page.get_by_role('button',name='전송 및 유료 API 호출 승인').click()
        await page.wait_for_function("document.querySelector('#task-title').textContent.includes('서버 AI 분석 중')")
        assert await page.locator('#ai').is_disabled() and await page.locator('#start-scan-ai').is_disabled()
        assert await page.locator('#task-cancel').is_visible()
        await page.wait_for_function("document.querySelector('#source').textContent.includes('AI 제안')")
        await page.wait_for_function("document.querySelector('#task-status').dataset.state==='done'")
        assert await page.locator('#ai').is_enabled()
        # Use the same real timeout handler with a short test duration; never call an AI.
        await page.evaluate('''() => {
            const original=window.fetch;
            window.fetch=(url,options)=>String(url).includes('/api/ai/status')?new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})}):original(url,options);
        }''')
        await page.evaluate("() => action(document.getElementById('ai'),()=>networkApi('/api/ai/status',{timeoutMs:200}))")
        assert '초과' in await page.locator('#task-detail').inner_text()
        assert await page.locator('#ai').is_enabled() and await page.locator('#start-scan-ai').is_enabled()
        await page.evaluate("() => { action(document.getElementById('ai'),()=>networkApi('/api/ai/status',{timeoutMs:10000})); }")
        await page.wait_for_function("document.querySelector('#task-cancel').hidden===false")
        await page.locator('#task-cancel').click()
        await page.wait_for_function("document.querySelector('#task-status').dataset.state==='error'")
        assert '중단' in await page.locator('#task-detail').inner_text()
        assert await page.locator('#ai').is_enabled() and await page.locator('#start-scan-ai').is_enabled()
        await page.evaluate("() => {window.fetch=async()=>new Response(JSON.stringify({error:'검증용 서버 오류'}),{status:500,headers:{'Content-Type':'application/json'}})}")
        await page.evaluate("() => action(document.getElementById('ai'),()=>networkApi('/api/ai/status'))")
        assert '검증용 서버 오류' in await page.locator('#task-detail').inner_text()
        assert await page.locator('#ai').is_enabled() and await page.locator('#start-scan-ai').is_enabled()
        assert not errors,errors
        await page.screenshot(path='/tmp/exam-progress-card.png',full_page=True)
        await browser.close()
        print('Progress UI passed: slow status/estimate, elapsed time, bounded preflight, consent cancel, delayed mocked AI, persistent status, timeout and cancel release BOTH buttons. No paid calls.')

asyncio.run(main())
