# Vercel 체험 배포

GitHub `oh9655-debug/exam` 저장소를 Vercel의 Add New → Project에서 Import하세요.

- Root Directory: 저장소 루트 (기본값)
- Framework Preset: Other. 저장소의 vercel.json에 Python 함수 라우팅이 정의되어 있습니다.
- Build Command / Output Directory: 별도 override 없이 기본값
- Environment Variables: `EXAM_SESSION_SECRET`에 본인이 생성한 32자 이상의 무작위 문자열을 입력하세요. Python으로 생성하려면 `python -c "import secrets; print(secrets.token_hex(32))"`를 사용합니다. 생성값은 GitHub에 올리지 마세요.
- 분석용 API 키는 Vercel 환경변수에 넣지 않습니다. 배포된 프로그램의 관리자 설정에서 비밀번호 5162로 로그인한 뒤 등록합니다. 자세한 내용은 AI_SETTINGS.md를 참고하세요.
- Deploy를 누르세요. Python 의존성은 requirements.txt에서 설치됩니다.

배포 후 samples/sample-text.pdf를 내려받아 업로드 → 문항 수정 → 통계를 확인하세요. 실제 Vercel 배포는 사용자 계정에서 진행해야 하며, 이 문서 자체가 배포 성공을 의미하지 않습니다.

## 임시 저장 제한

이번 구성은 무료 외부 DB/API 없이 기능을 시험하는 체험 배포입니다. 시험지는 서명된 세션 쿠키를 기준으로 브라우저별 `/tmp` 하위에 분리됩니다. 쿠키 삭제, 함수 재시작, 다른 인스턴스 연결, 재배포 시 자료가 없을 수 있습니다. 영구 저장·운영 배포가 아닙니다. 중요한 원본과 개인정보는 올리지 마세요. 자료 영구 보관은 별도 데이터베이스·파일 저장소가 필요합니다.

Vercel 함수의 요청 제한은 그대로 4MB입니다. 사용자는 최대 100MB/30페이지 파일을 선택할 수 있으며, 3MB 초과 파일은 브라우저에서 업로드용 사본을 준비합니다. PDF는 먼저 구조를 정리하고, 필요하면 긴 변 최대 1800px/JPEG 88%의 페이지 이미지 사본으로 만듭니다. 이미지 파일은 긴 변 최대 2000px/JPEG 90%를 사용합니다. 그래도 3MB를 넘으면 더 낮은 화질로 만들지 않고 페이지 분할을 안내합니다. 원본은 서버가 아닌 등록한 브라우저 IndexedDB에 보관하며, AI에는 전송한 사본을 사용합니다. 배포 프로젝트/플랜의 함수 실행 시간·패키지 크기 한도도 적용됩니다.

로컬 실행 방식은 그대로 유지됩니다. Vercel 전용 동작은 플랫폼의 VERCEL=1 환경에서 적용됩니다. EXAM_SESSION_SECRET은 세션 보호용이며 유료 AI API 키가 아닙니다.
