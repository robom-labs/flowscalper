# ROBOM FlowScalper

실제 공개 암호화폐 무기한선물 시장데이터를 연구하되, 모든 체결을 1,000 USDT 내부 가상계좌에서만 시뮬레이션하는 로컬 애플리케이션입니다. 로그인, 거래소 API 키, OpenAI 키, 지갑이 필요하지 않으며 실제 주문 기능은 없습니다.

현재 구현은 Wave 00의 오프라인 fixture 수직 슬라이스입니다. 전체 기능과 실행 증거는 후속 Wave에서 계속 완성됩니다.

## 개발 시작

Python 3.12, Node.js 22 이상, uv, pnpm이 필요합니다.

```bash
make setup
make build
make fixture-demo
```

브라우저에서 `http://127.0.0.1:8765`를 엽니다. `OFFLINE FIXTURE`, `PAPER`, `실제 주문 없음`이 동시에 보여야 합니다.

## 검증

```bash
make lint
make typecheck
make test
make build
make e2e
```

## 안전 경계

`REAL_TRADING=true`는 시작과 빌드에서 거부됩니다. 서버는 `127.0.0.1`에만 바인딩하며, 공개시세가 검증되지 않은 fixture 상태를 LIVE로 표시하지 않습니다.

