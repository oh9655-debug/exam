# Vercel 체험 배포

GitHub `oh9655-debug/exam` 저장소를 Vercel의 Add New → Project에서 Import하세요.

- Root Directory: 저장소 루트 (기본값)
- Framework Preset: Other. 저장소의 vercel.json에 Python 함수 라우팅이 정의되어 있습니다.
- Build Command / Output Directory: 별도 override 없이 기본값
- Environment Variables: `EXAM_SESSION_SECRET`에 본인이 생성한 32자 이상의 무작위 문자열을 입력하세요. Python으로 생성하려면 `python -c "import secrets; print(secrets.token_hex(32))"`를 사용합니다. 생성값은 GitHub에 올리지 마세요.
- 분석용 API 키는 Vercel 환경변수에 넣지 않습니다. 배포된 프로그램의 관리자 설정에서 비밀번호 5162로 로그인한 뒤 등록합니다. 자세한 내용은 AI_SETTINGS.md를 참고하세요.
- Deploy를 누르세요. Python 의존성은 requirements.txt에서 설치됩니다.

배포 후 samples/sample-text.pdf를 내려받아 업로드 → 문항 수정 → 통계를 확인하세요. 실제 Vercel 배포는 사용자 계정에서 진행해야 하며, 이 문서 자체가 배포 성공을 의미하지 않습니다.

## 저장 방식 · 브라우저 보관

시험지 원본·전송 사본·문항·AI 제안·교사 수정 이력은 등록한 브라우저의 IndexedDB에 보관합니다. 서버의 임시 디스크는 해당 요청을 처리하는 캐시로만 사용합니다. 서버 작업마다 전송 사본과 문항 스냅샷을 함께 보내므로 다른 Vercel 인스턴스가 처리해도 이전 서버의 파일에 의존하지 않습니다. 목록·재열기는 브라우저 보관본을 우선 사용합니다.

이전 버전에서 서버에만 저장한 기록은 서버가 사라졌으면 복원할 수 없습니다. 원본 파일을 한 번 다시 등록해야 합니다. 새 버전에서 등록한 기록은 같은 브라우저의 새로고침·서버 재시작·재배포 후에도 다시 열 수 있습니다. 브라우저 저장 데이터 삭제·자동 정리·다른 기기에서는 자료를 공유하지 않습니다. 공용 클라우드 DB 또는 기기 간 동기화 기능은 없습니다.

Vercel 함수의 요청 제한은 4MB입니다. 사용자는 최대 100MB/30페이지 파일을 선택하고, 3MB 초과 파일은 브라우저에서 전송용 사본을 준비합니다. 원본 검토와 전송 사본 구분은 LARGE_UPLOADS.md를 참고하세요. 문항 스냅샷은 UTF-8 700KB 이하로 제한하고, 초과 시 변경 내용을 임의로 버리지 않고 오류를 안내합니다.

분석용 API 키는 앱의 관리자 설정에서 입력합니다. EXAM_SESSION_SECRET은 기존 서버 비밀값을 유지해야 관리자 로그인과 키·비용 쿠키를 읽을 수 있습니다. 실제 배포 URL에 대한 직접 HTTP 검사는 개발 환경의 네트워크 정책으로 제한될 수 있습니다.
