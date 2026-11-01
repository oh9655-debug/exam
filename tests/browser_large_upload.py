"""Optional Chromium regression: >4MB scanned-style PDF, exact original, text extraction.
Run a local app first. Requires playwright, PyMuPDF and CHROMIUM_PATH if using system Chromium.
No paid API calls. Uses temporary synthetic documents.
"""
import hashlib
import os
from pathlib import Path
import random
import tempfile
import fitz
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as temp:
    path=Path(temp)/'large-exam.pdf'
    document=fitz.open()
    rng=random.Random(1986)
    data=rng.randbytes(1000*1500*3).translate(bytes(240+(i%16) for i in range(256)))
    pix=fitz.Pixmap(fitz.csRGB,1000,1500,data,False)
    for n in range(2):
        page=document.new_page()
        page.insert_image(page.rect,pixmap=pix)
        page.insert_text((40,80),f'{n+1}. 방정식 2x + 3 = 11을 풀어라. [5점]',fontname='korea',fontsize=16)
    document.save(path);document.close()
    assert path.stat().st_size>4*1024*1024
    expected=hashlib.sha256(path.read_bytes()).hexdigest()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'))
        page=browser.new_page(viewport={'width':1400,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:5000')
        page.locator('[name=file]').set_input_files(str(path))
        page.locator('[name=school]').fill('큰 파일 검증중')
        with page.expect_response(lambda r:r.url.endswith('/api/exams') and r.request.method=='POST',timeout=120000) as response:
            page.get_by_role('button',name='시험지 등록 및 로컬 인식 →').click()
        r=response.value
        assert r.status==201,r.text()
        eid=r.json()['id']
        page.locator('#question-list button').nth(1).wait_for()
        assert page.locator('#question-list button').count()==2
        assert '방정식' in page.locator('#q-text').input_value()
        page.wait_for_function("document.querySelector('#source-note').textContent.includes('변경 없는 원본')")
        actual=page.evaluate('''async id=>{const x=await ExamUpload.get(id);const b=await crypto.subtle.digest('SHA-256',await x.file.arrayBuffer());return [...new Uint8Array(b)].map(n=>n.toString(16).padStart(2,'0')).join('')}''',eid)
        assert actual==expected
        original_response=page.request.get(f'http://127.0.0.1:5000/api/exams/{eid}/original')
        assert len(original_response.body())<3*1024*1024
        assert len(original_response.body())<path.stat().st_size
        with page.expect_download() as download:
            page.get_by_text('원본 다운로드',exact=True).click()
        assert hashlib.sha256(Path(download.value.path()).read_bytes()).hexdigest()==expected
        page.reload()
        page.locator('#exam-list button').first.click()
        page.wait_for_function("document.querySelector('#source-note').textContent.includes('변경 없는 원본')")
        assert not errors,errors
        print(f'Large PDF passed: {path.stat().st_size:,} bytes -> {len(original_response.body()):,} bytes, 2 text questions, exact original download and reload.')
        image_path=Path(temp)/'large-image.png'
        image_data=rng.randbytes(1600*2400*3).translate(bytes(240+(i%16) for i in range(256)))
        large_pix=fitz.Pixmap(fitz.csRGB,1600,2400,image_data,False)
        large_pix.save(image_path)
        assert image_path.stat().st_size>3*1024*1024
        page.goto('http://127.0.0.1:5000')
        page.locator('[name=file]').set_input_files(str(image_path))
        page.locator('[name=school]').fill('큰 이미지 검증')
        with page.expect_response(lambda r:r.url.endswith('/api/exams') and r.request.method=='POST',timeout=120000) as image_response:
            page.get_by_role('button',name='시험지 등록 및 로컬 인식 →').click()
        assert image_response.value.status==201
        page.wait_for_function("document.querySelector('#source-note').textContent.includes('변경 없는 원본')")
        with page.expect_download() as image_download:
            page.get_by_text('원본 다운로드',exact=True).click()
        assert hashlib.sha256(Path(image_download.value.path()).read_bytes()).hexdigest()==hashlib.sha256(image_path.read_bytes()).hexdigest()
        assert not errors,errors
        print('Large PNG passed: original preserved; transfer JPEG registered under server limit.')
        browser.close()
