# ADR-016. 깊이보정 흐름과 다중호가 공정가 SHADOW 전략

## 상태

Accepted, 2026-08-25.

## 문제

기존 E 전략은 top5·top10 수량 불균형과 최우선 microprice를 함께 요구하고, F 전략은 공격 체결 notional과 추세 레짐을 본다. 이들은 유용한 가설이지만 여러 호가 단계의 가격 간격을 포함한 공정가와, 같은 OFI라도 현재 호가 깊이가 얕을수록 가격영향이 커질 수 있다는 두 가설을 독립적으로 비교하지 못한다.

현재 저장된 LIVE_PUBLIC 독립계좌 표본은 여섯 기존 전략 모두 수익성을 입증하지 못했다. 따라서 신규 가설을 공동계좌 ACTIVE로 승격하거나 자연신호를 만들기 위해 기존 임계값을 낮추는 것은 허용하지 않는다.

## 공식 연구 근거와 적용 한계

- Cont, Kukanov, Stoikov의 [The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402)는 짧은 구간 가격변화가 OFI와 대체로 선형이며 기울기가 시장 깊이에 반비례한다는 결과를 보고한다.
- Stoikov의 [The Micro-Price](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694)는 주문장 상태로 조정한 공정가가 단순 mid보다 단기 가격을 더 잘 설명할 수 있다는 가설을 제시한다.
- Zheng, Moulines, Abergel의 [Price Jump Prediction in Limit Order Book](https://arxiv.org/abs/1204.1381)은 다단계 호가 간격, 유동성 균형과 체결 부호가 단기 점프 분류에 유용할 수 있음을 보인다.
- Martin 외의 [Mind the Gaps: Short-Term Crypto Price Prediction](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4351947)은 암호화폐 주문장에서 여러 단계 가격 간격을 반영한 공정가 계열의 단기 방향 예측력을 보고한다.
- Binance의 [공식 WebSocket 시장데이터 문서](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams)에서 공개 depth·trade 스트림과 로컬 주문장 sequence 절차를 확인한다.

연구 대상 거래소·기간·시장구조는 현재 Binance USD-M PAPER 환경과 동일하지 않다. 위 자료는 신호 가설의 근거일 뿐 수익성, 현재 임계값이나 실제 주문 가능성을 증명하지 않는다.

## 결정

1. `MULTILEVEL_MICROPRICE_MOMENTUM_V1`을 G 전략으로 추가한다. top10 bid·ask 가격×수량에서 각 방향 VWAP을 구하고 반대편 총수량으로 가중한 다중호가 공정가를 계산한다.
2. G는 공정가 변위, 최우선 microprice, 250ms·3s OFI, 1s 공격 체결, 가격반응이 같은 방향으로 750ms 이상 지속될 때만 비용 게이트로 보낸다.
3. `DEPTH_ADJUSTED_OFI_IMPULSE_V1`을 H 전략으로 추가한다. 3s OFI 수량을 mid notional로 바꾼 뒤 top10 양방향 평균 깊이 notional로 나눈 bp 값을 계산한다.
4. H는 현재 방향의 깊이보정 OFI robust z가 2.0 이상이고 250ms·3s OFI, 1s 체결, microprice와 가격반응이 500ms 이상 정렬될 때만 비용 게이트로 보낸다.
5. robust z는 현재 snapshot보다 이전인 동일 종목 1,200개 이하 표본만 사용하고 기존 증분 정렬창에서 삽입·퇴출한다.
6. G/H는 EXPERIMENTAL·SHADOW가 기본이며 LONG·SHORT를 모두 평가한다. 각각 BASE·STRESS 독립 PAPER 계좌만 사용하고 공동 1,000 USDT 계좌에는 진입하지 않는다.
7. G/H는 기존 TREND 40/60 분할청산, 최소 구조거리 0.30%, 비용후 순손익비 1.20, 실제 bid·ask·수수료·슬리피지·깊이 제한을 그대로 사용한다.
8. 레지스트리는 8개 전략과 BASE·STRESS 16계좌가 된다. 실제 주문, private API, API Key, 인증과 wallet 경로는 계속 없다.

## 검증

- top10 공정가와 깊이보정 OFI 계산을 독립 참조식과 대조한다.
- G/H LONG·SHORT 대칭 적격, 각 핵심 거부 사유, WARMUP·DEGRADED·SHOCK 차단과 비용 거부를 검사한다.
- 이벤트시간 지속성, 조건 파괴 시 초기화, H의 과거 prefix robust z와 포화창 퇴출을 검사한다.
- A~H 모든 전략의 LONG·SHORT와 TP1→TP2·초기 SL을 같은 PAPER 체결엔진으로 종단 검증한다.
- 저장 공개시장 Run을 두 번 replay해 checksum과 평가·후보·거래 수가 일치하는지 확인한다.
- 실제 브라우저에서 8개 전략, 16계좌, A/B ACTIVE와 C~H SHADOW, 실제 주문 0을 확인한다.

## 한계

결정론적 테스트와 replay 통과는 자연 LIVE 신호, 장시간 안정성 또는 수익성을 증명하지 않는다. 신규 전략의 자연 적격·체결이 관찰되지 않으면 `NOT_OBSERVED`, 30개 미만 완료표본은 `표본 부족`, 수익성은 `NOT_PROVEN`으로 기록한다.
