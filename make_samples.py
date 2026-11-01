"""Synthetic samples only. No real school or student data."""
from pathlib import Path
import fitz

folder = Path(__file__).parent / 'samples'
folder.mkdir(exist_ok=True)
doc = fitz.open()
p = doc.new_page()
p.insert_text((50, 60), '기능 검증용 가상 시험지 (실제 기출문제 아님)', fontname='korea', fontsize=16)
p.insert_text((50, 110), '1. 일차방정식 2x + 3 = 11 을 풀어라. [4점]', fontname='korea', fontsize=13)
p.insert_text((50, 180), '2. 삼각형의 두 각이 50도, 60도일 때 나머지 각을 구하여라. [5점]', fontname='korea', fontsize=13)
p.insert_text((50, 250), '3. 함수 y = 2x + 1 에서 다음을 구하여라. [6점]', fontname='korea', fontsize=13)
p.insert_text((65, 280), '(1) x = 2 일 때 y의 값', fontname='korea', fontsize=13)
p2 = doc.new_page()
p2.insert_text((50, 70), '(2) y = 9 일 때 x의 값', fontname='korea', fontsize=13)
doc.save(folder / 'sample-text.pdf')
image = doc[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5))
image.save(folder / 'sample-image.png')
scanned = fitz.open()
for page in doc:
    target = scanned.new_page(width=page.rect.width, height=page.rect.height)
    target.insert_image(target.rect, stream=page.get_pixmap().tobytes('png'))
scanned.save(folder / 'sample-scan.pdf')
print('Created 3 synthetic sample files in', folder)
