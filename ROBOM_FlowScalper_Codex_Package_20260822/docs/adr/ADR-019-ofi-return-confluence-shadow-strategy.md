# ADR-019. OFI·단기수익률 동행 SHADOW 전략

## 상태

Accepted — 2026-08-25.

## 배경

현재 G/H는 다중호가 공정가와 깊이보정 OFI 충격을 각각 평가하지만, 최근 가격 경로가 실제로 같은 방향으로 이동했는지를 독립 조건으로 요구하지 않는다. Cont·Kukanov·Stoikov는 짧은 구간의 가격 변화가 호가 깊이에 비례해 보정된 order-flow imbalance와 관계됨을 보였다. Schmalz의 2026년 BTC/USDT 연구는 OFI 단독 결과가 표본과 수정에 민감하고, 짧은 자기회귀 수익률과 결합했을 때만 일부 구간에서 추가 설명력이 있음을 보고한다.

이 근거는 수익성을 증명하지 않는다. 특히 단일 거래쌍·거래소·짧은 표본 결과를 전체 코인과 미래 시장에 일반화할 수 없다. 따라서 새로운 조건은 기존 ACTIVE 전략에 섞거나 임계값을 낮추는 근거가 아니라, 독립 PAPER 계좌에서 검증할 연구 가설로만 사용한다.

## 결정

1. `OFI_RETURN_CONFLUENCE_V1`을 I 전략으로 추가한다.
2. 기본 mode는 `SHADOW`이고 LONG·SHORT 모두 켜며 BASE·STRESS 계좌를 각각 1,000 USDT로 생성한다.
3. 현재 snapshot보다 3초 이전의 가장 가까운 동일 종목 prefix 가격을 사용한다. 기준 가격은 목표 시각보다 최대 1.5초까지만 오래될 수 있고 미래 timestamp는 제외한다.
4. spread 8bp 이하, 방향성 깊이보정 OFI robust z 1.5 이상, 250ms·3s OFI 정렬, 방향성 3초 수익률 2bp 이상, microprice 변위 0.20bp 이상, 가격반응 효율 0.30 이상을 모두 요구한다.
5. 정렬은 실제 event timestamp 기준 1,000ms 이상 지속돼야 하며 하나라도 깨지면 확인시각을 초기화한다.
6. 공격 체결 imbalance는 독립 필수조건으로 추가하지 않는다. I는 OFI와 단기 가격경로의 결합 가설을 분리 검증하며, E/F/H의 체결·호가 조건을 그대로 복제하지 않는다.
7. 다른 TREND 전략과 동일한 최소 0.30% 구조 stop, 3.2R target, 최소 예상 왕복비용 13bp와 최종 순손익비 1.20 gate를 사용한다.
8. 전략 구현 변경으로 `STRATEGY_VERSION`을 갱신한다. 이전 전략 버전 표본은 현재 성과에서 제외한다.
9. 레지스트리는 9개 전략, 독립 BASE·STRESS 18계좌가 된다. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 없다.

## 검증

- LONG·SHORT 대칭 적격 fixture와 모든 핵심 거절 reason code를 검사한다.
- 비용비중 거절, prefix 기준가격 부재, 미래 표본 무시, robust z, 1,000ms 지속성과 reset을 검사한다.
- 아홉 전략 모두에 대해 양방향 계획, TP1·TP2·SL, BASE·STRESS 비용과 복구 호환성을 매개변수화해 검사한다.
- 저장한 실제 공개시장 이벤트를 동일 코드 경로로 두 번 replay해 결정성과 PAPER/auth 불변조건을 확인한다.
- LIVE 결과는 표본 수·기대값·비용·낙폭과 함께 기록하고 수익성 근거로 표현하지 않는다.

## 근거와 한계

- Cont, Kukanov, Stoikov, *The Price Impact of Order Book Events* — https://arxiv.org/abs/1011.6402
- Michael Schmalz, *Order Flow Imbalance and Short-Horizon BTC/USDT Returns: A Signal That Kept Needing More Scrutiny* — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7227998
- *Exploring Microstructural Dynamics in Cryptocurrency Limit Order Books* — https://arxiv.org/abs/2506.05764

모든 threshold는 검증을 시작하기 위한 보수적 PAPER 연구값이다. 자연신호가 적더라도 거래 수를 만들기 위해 완화하지 않는다. 승격은 충분한 현재버전 LIVE_PUBLIC 표본, 비용후 기대값, Profit Factor, 낙폭, 레짐·종목 분산과 별도 승인 전에는 금지한다.
