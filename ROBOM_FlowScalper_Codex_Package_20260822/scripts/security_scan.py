"""소스·UI·패키지에 실제 주문·자격 증명 경로가 없는지 정적 검사한다."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (PROJECT_ROOT / "backend" / "app", PROJECT_ROOT / "frontend" / "src")
FORBIDDEN = (
    "/fapi/v1/order",
    "/v5/order/create",
    "x-mbx-apikey",
    "api_secret",
    "secret_key",
    "private_key",
    "withdraw",
    "wallet_seed",
    'type="password"',
    "live_trading",
)


def main() -> None:
    checked_files = 0
    violations: list[dict[str, object]] = []
    for root in SOURCE_ROOTS:
        for path in sorted(
            item for item in root.rglob("*") if item.suffix in {".py", ".ts", ".tsx"}
        ):
            checked_files += 1
            text = path.read_text(encoding="utf-8").lower()
            for fragment in FORBIDDEN:
                if fragment in text:
                    violations.append(
                        {
                            "file": str(path.relative_to(PROJECT_ROOT)),
                            "fragment": fragment,
                        }
                    )
    secret_files = [
        str(path.relative_to(PROJECT_ROOT))
        for path in PROJECT_ROOT.rglob("*")
        if path.is_file()
        and path.name.lower() in {".env", ".env.local", "id_rsa", "credentials.json"}
        and not any(part in {".venv", "node_modules"} for part in path.parts)
    ]
    result = {
        "status": "PASS" if not violations and not secret_files else "FAIL",
        "checked_source_files": checked_files,
        "forbidden_fragments": list(FORBIDDEN),
        "violations": violations,
        "secret_like_files": secret_files,
        "real_order_path": False if not violations else "REVIEW_REQUIRED",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if violations or secret_files:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
