# ADR-077 SIHO 공개증거와 LIVE PAPER 연구부하 격리

## 상태

Accepted.

## 배경

사용자가 지정한 `1mJDNm4Yko4` 영상에서 직접 읽은 YouTube 공개 player response와 채널
About HTML은 전체 channel id `UC7Z6zXw5q1vou0DgPZ80GBA`, handle `@siholab`, 채널명
`Siho LAB`을 일치시켰다. 공식 채널의 공개 탭은 `동영상`과 `Shorts` 두 개이며, 2026-08-27
수집 시 각각 32개와 27개 항목을 반환했다. 기준 영상과 최신 두 영상의 공개 설명은 동일한
checksum이었고 EMA, RSI, 시장구조, retest, stop loss, take profit, trailing stop을 언급했지만
timeframe, 수치, 조건 순서, position sizing과 trailing parameter는 공개하지 않았다.

Wave 99의 21,600초 post-quarantine 관찰이 실행되는 동안에는 전체 영상 다운로드, 로컬 ASR,
프레임 추출, 대형 replay와 전체 회귀테스트를 시작하지 않았다. 해당 관찰은 21,600.025초를
채운 뒤 별도 FAIL 증거로 종료됐으며, 이후에만 영상 연구도구와 재개 가능한 수집 경로를
준비했다.

## 결정

1. 공식 채널 동일성은 기준 영상 player response, 채널 About HTML, 공식 Atom feed의 세 공개
   소스를 대조해 확정하고 `evidence/SIHO_CHANNEL_IDENTITY.json`에 checksum과 함께 보존한다.
2. 채널 이름 검색 결과나 추천영상은 공식 영상 목록에 섞지 않는다. 채널의 `동영상`과
   `Shorts` 탭을 각각 pagination 끝까지 순회하며 같은 video id가 양쪽에 있으면 수집을
   실패시킨다.
3. Git에는 원본 영상, 전체 transcript와 대량 frame을 넣지 않는다. 공개 metadata, checksum,
   timestamp evidence, 필요한 짧은 paraphrase, 분석 코드와 재현 명령만 둔다.
4. 제목·썸네일·성과 주장만으로 전략 규칙이나 수익성을 확정하지 않는다. 현재 description의
   indicator·기능 목록은 `PUBLIC_AMBIGUOUS`이며 exact parameter가 아니다.
5. 영상에 적힌 Bybit API·webhook·실제 주문 경로는 FlowScalper에 가져오지 않는다. 본
   저장소는 공개시장 입력과 내부 PAPER 체결만 유지한다.
6. 6시간 관찰 중에는 HTML metadata와 채널 목록처럼 짧고 비침습적인 외부 조회만 허용한다.
   전체 영상 다운로드, ASR, 프레임 추출, 대형 replay, ledger integrity와 전체 빌드는 관찰
   종료 뒤 수행한다.
7. SIHO의 공개 entry·exit·timeframe·trailing·sizing 규칙이 모두 exact로 확인되기 전에는
   `SIHO_PUBLIC_CURRENT_BASELINE_V1`을 SHADOW에 등록하지 않는다. 누락 항목은 명시적
   `BLOCKED_MISSING_*`으로 남긴다.
8. 공식 Bybit trailing 공식을 우리 연구모듈에 쓸 경우에도 SIHO 원본규칙이 아니라 별도
   `RESEARCH_HYPOTHESIS` exit module로 사전등록한다.
9. 공개 caption은 원본 JSON3 checksum과 시간순 정규화 JSONL checksum을 모두 남긴다. 공개
   caption이 없는 장문영상만 로컬 Whisper ASR 대상으로 지정하며, 도구·모델·timeline
   checksum이 없으면 `BLOCKED_TOOL_MISSING` 또는 `NOT_RUN_REQUIRED`를 유지한다.
10. 전체 검토 범위는 최신 장문 30개, 최근 12개월, 전략 keyword 적중 영상, 설명란에서
    연결된 영상과 기준 영상의 합집합을 채널 순서대로 고정한다. hydration 전에는 범위를
    추정하지 않고 `PENDING_HYDRATED_SCOPE`로 둔다.
11. 10초 overview frame과 장면전환 frame은 Git 밖의 `data/research/siho/`에 보관하고 각
    timestamp·checksum만 manifest에 남긴다. 자산 수집만으로 검토 완료로 바꾸지 않으며,
    timeline·설정창·주문화면·entry/stop/target·성과표·마지막 주의사항을 실제로 확인한 뒤에만
    영상별 `full_video_review_status = COMPLETE`로 바꾼다.

## 결과

- 공식 채널과 다른 동명이인 영상을 섞는 오류를 방지한다.
- 영상의 마케팅 문구, 화면 추정과 exact 공개규칙이 분리된다.
- 6시간 안정성 관찰과 영상 연구의 자원 사용이 서로의 증거를 오염시키지 않는다.
- 현재 SIHO baseline은 `CURRENT_STRATEGY = UNCONFIRMED`이며 수익성도 `NOT_PROVEN`이다.
- 59개 공개영상 metadata와 설명 원문 검사는 완료했다. 장편 32개는 공개 caption이 없어 모두
  로컬 ASR 대상이며 총 재생시간은 25,843초다.
- `evidence/SIHO_FRAME_EVIDENCE_MANIFEST.json`은 장편 32개를 정확히 고정하고 현재
  `ASSETS_COLLECTED_REVIEW_NOT_RUN`이다. ASR timeline 32개와 overview frame 2,585개,
  scene frame 4개의 파일·byte·checksum을 재검증했지만 timeline·frame·전체 영상 내용 검토는
  모두 0개다. frame 파일 존재와 전체 영상 검토 완료를 계속 구분한다.
- 수집기는 영상별 원본을 임시로만 보유하고 transcript·overview·scene 자산을 Git 밖에
  checkpoint한다. 중단 뒤 `--resume`으로 완료 영상을 건너뛰며 특정 `--video-id`만 먼저
  검증할 수도 있다.
- 로컬 연구도구는 `yt-dlp 2026.08.19`(Unlicense,
  `https://github.com/yt-dlp/yt-dlp`), `FFmpeg 9.0.1_1`(Homebrew bottle,
  GPL-3.0-or-later, `https://ffmpeg.org/`), `openai-whisper 20250625`(MIT,
  `https://github.com/openai/whisper`)다. 버전과 라이선스는 설치 package metadata와
  Homebrew formula metadata에서 읽었으며 프로그램 런타임 의존성은 아니다.
- macOS 재현 명령은 다음과 같다. 도구의 실제 경로는 환경마다 명시적으로 넘기고 Whisper
  model은 외장 연구 cache에 둔다.

```bash
make research-siho-review-assets \
  SIHO_YT_DLP=/path/to/yt-dlp \
  SIHO_FFMPEG=/path/to/ffmpeg \
  SIHO_ASR=/path/to/whisper \
  SIHO_ASR_MODEL=small \
  SIHO_ASR_MODEL_DIR=data/research/siho/models \
  SIHO_ASR_DEVICE=cpu \
  SIHO_ASR_THREADS=4
```

- 자산 수집·ASR·frame 추출만으로 Wave 1을 완료로 판정하지 않는다. 32개 timeline과 frame을
  처음부터 끝까지 실제로 검토하고 timestamp 증거표를 채우기 전에는
  `CURRENT_STRATEGY = UNCONFIRMED`, 수익성 `NOT_PROVEN`을 유지한다.
