# 11. Security and Privacy

## 11.1 Threat model

Version 0.1 is a local paper application. The highest-priority risks are:

- accidental implementation of a real-order path;
- false LIVE status;
- corrupted/stale market data;
- remote exposure of the local dashboard;
- dependency compromise;
- data loss or disk exhaustion;
- misleading performance calculations.

## 11.2 No-secret architecture

The application must not need or accept:

- exchange username/password;
- API key/secret/passphrase;
- wallet seed/private key;
- OpenAI key;
- cloud database credentials.

Do not include placeholder fields that invite users to paste secrets.

## 11.3 Structural real-trading block

Required controls:

- no private exchange adapter package;
- no order-placement endpoint strings in production source except negative tests/documentation;
- a domain-level `RealTradingDisabledError`;
- mode enum without an operational live value;
- API and UI do not expose a live toggle;
- test scanning imports/routes for forbidden functionality;
- build-time assertion that `REAL_TRADING=false`.

## 11.4 Network exposure

Default bind:

```text
127.0.0.1
```

If a user attempts `0.0.0.0`, fail unless an explicit advanced security configuration is present. v0.1 does not need remote access.

Use restrictive CORS and WebSocket origins.

## 11.5 Input validation

Validate all venue payloads:

- expected event type;
- symbol belongs to current venue metadata;
- numeric strings parse and are finite/non-negative where required;
- sequence continuity;
- timestamp sanity;
- price/quantity bounds;
- maximum message size.

Malformed data must not reach the strategy engine.

## 11.6 Dependency hygiene

- pin dependencies and commit lockfiles;
- prefer maintained libraries;
- generate a third-party notice/license report;
- run dependency vulnerability checks where available;
- avoid installing an unofficial exchange SDK when direct protocol handling is simple;
- never execute remote code from market-data payloads.

## 11.7 Logging

Logs may contain symbols, prices and Run IDs. They must not contain secrets because no secrets are accepted.

Support log rotation and redaction of local filesystem/user names in exported diagnostics where practical.

## 11.8 Data integrity

- transactional state changes;
- append-only finalized trade records;
- checksums/manifest for replay bundles;
- schema/version migrations;
- backup/export option;
- corruption detection and safe pause.

## 11.9 Privacy

The product is local-first. No telemetry is sent by default. If any optional telemetry is ever added, it must be opt-in and outside v0.1.
