# CLAUDE.md

이 파일은 이 리포지토리에서 작업할 때 Claude Code가 따라야 할 지침을 담습니다.

## GUI 미리보기

- HTML/GUI(템플릿, 정적 파일, Streamlit 화면 등)를 수정하면 **매번** 헤드리스 Chromium으로 렌더링해 PNG 스크린샷으로 결과를 보여줄 것.
- 실행 환경에 브라우저가 없을 수 있으니, 로컬 CLI 환경이라면 먼저 Playwright/Chromium 설치 여부를 확인하고, 없으면 설치 방법을 안내할 것.
  - 참고: Claude Code on the web 원격 환경에는 Chromium이 이미 설치되어 있고 Playwright가 이를 찾도록 구성되어 있음(`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`). 이 경우 `playwright install`을 실행하지 말 것.
