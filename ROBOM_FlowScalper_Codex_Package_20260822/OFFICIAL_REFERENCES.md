# Official References and Primary Research

Accessed: 2026-08-22

Codex must verify current behavior again during implementation because exchange APIs can change.

## Binance official documentation

- USDⓈ-M Futures WebSocket market streams connection and limits  
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect

- Current USDⓈ-M public stream catalog  
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public

- Current USDⓈ-M market stream catalog  
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market

- WebSocket base URL split and migration notice  
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice

- USDⓈ-M REST market data catalog  
  https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

- Public endpoints terminology/common definitions  
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition

- USDⓈ-M change log  
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log

Important current facts to re-check in code:

- public stream endpoint families may be separated by data type;
- a single connection has a documented maximum stream count;
- connections have a documented finite lifetime and require reconnect handling;
- book/depth streams support high-frequency updates;
- conditional/private order changes are irrelevant to v0.1 because real/private calls are forbidden.

## Bybit official documentation

- V5 WebSocket connection and public-topic authentication behavior  
  https://bybit-exchange.github.io/docs/v5/ws/connect

- Public orderbook snapshot/delta processing  
  https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook

- Public trade stream  
  https://bybit-exchange.github.io/docs/v5/websocket/public/trade

- Public ticker stream  
  https://bybit-exchange.github.io/docs/v5/websocket/public/ticker

- Instrument metadata and pagination  
  https://bybit-exchange.github.io/docs/v5/market/instrument

- Public ticker snapshot/24-hour turnover  
  https://bybit-exchange.github.io/docs/v5/market/tickers

## Codex official guidance

- Codex CLI  
  https://developers.openai.com/codex/cli

- AGENTS.md guidance  
  https://developers.openai.com/codex/agent-configuration/agents-md

- Codex best practices  
  https://developers.openai.com/codex/learn/best-practices

- Using PLANS.md / ExecPlans for multi-hour work  
  https://developers.openai.com/cookbook/articles/codex_exec_plans

- Long-horizon Codex tasks  
  https://developers.openai.com/blog/run-long-horizon-tasks-with-codex

## Chart library

- TradingView Lightweight Charts open-source repository  
  https://github.com/tradingview/lightweight-charts

The project uses the open-source chart library only. It does not require a TradingView account, alert or webhook.

## Primary market-microstructure research

- Cont, Kukanov, Stoikov — The Price Impact of Order Book Events  
  https://arxiv.org/abs/1011.6402

- Stoikov — The Micro-Price: A High Frequency Estimator of Future Prices  
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694

- Exploring Microstructural Dynamics in Cryptocurrency Limit Order Books (100 ms crypto LOB study)  
  https://arxiv.org/html/2506.05764v2

- Explainable Patterns in Cryptocurrency Microstructure  
  https://arxiv.org/html/2602.00776

- Navigating the Fill Probability vs. Post-Fill Returns Trade-Off  
  https://arxiv.org/html/2502.18625v2

Research use policy:

- These papers motivate features and conservative execution design.
- They do not prove that the proposed strategy will be profitable.
- The application must validate hypotheses using recorded live-paper data and costs.
