"""localhost에만 바인딩하고 선택적으로 대시보드 브라우저를 연다."""

from __future__ import annotations

import os
import threading
import webbrowser

import uvicorn


class RemoteBindingDisabledError(RuntimeError):
    """v0.1에서 원격 노출을 시도하면 부팅 전에 차단한다."""


def validate_local_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized not in {"127.0.0.1", "localhost", "::1"}:
        raise RemoteBindingDisabledError(f"원격 바인딩은 v0.1에서 비활성화됩니다: {host}")
    return normalized


def main() -> None:
    host = validate_local_host(os.environ.get("ROBOM_HOST", "127.0.0.1"))
    port = int(os.environ.get("ROBOM_PORT", "8765"))
    if not 1 <= port <= 65_535:
        raise ValueError("ROBOM_PORT는 1..65535 범위여야 합니다.")
    url = f"http://{host if host != '::1' else '[::1]'}:{port}"
    print(f"ROBOM FlowScalper PAPER: {url}")
    print("종료: 이 터미널에서 Ctrl+C")
    if os.environ.get("ROBOM_OPEN_BROWSER", "false").lower() in {"1", "true", "yes"}:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "backend.app.main:app",
        host=host,
        port=port,
        reload=os.environ.get("ROBOM_RELOAD", "false").lower() in {"1", "true", "yes"},
        access_log=False,
        # 큰 localhost 상태를 탭마다 압축하면 메인 이벤트 루프가 반복 점유된다.
        ws_per_message_deflate=False,
    )


if __name__ == "__main__":
    main()
