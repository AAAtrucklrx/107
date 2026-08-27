"""Start an isolated anonymous or demo FastAPI server for browser E2E tests."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("anonymous", "demo"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    environment = "production" if args.mode == "anonymous" else "competition"
    origin = f"http://127.0.0.1:{args.port}"
    values = {
        "XIAOWO_ENV": environment,
        "XIAOWO_AUTH_MODE": args.mode,
        "XIAOWO_PUBLIC_ORIGIN": origin,
        "XIAOWO_ADMIN_IDS": "PB25111691" if args.mode == "demo" else "",
        "XIAOWO_WEB_SEARCH_ENABLED": "false",
        "XIAOWO_INGESTION_WORKER_ENABLED": "false",
        "XIAOWO_APP_DB_PATH": str(data_dir / "xiaowo.db"),
        "XIAOWO_REVIEW_DB_PATH": str(data_dir / "review.db"),
        "XIAOWO_WEB_EVIDENCE_DIR": str(data_dir / "web_evidence"),
        "XIAOWO_PUBLISHED_CHROMA_DIR": str(data_dir / "chroma"),
        "XIAOWO_PUBLISHED_BM25_DIR": str(data_dir / "bm25"),
    }
    os.environ.update(values)
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    import uvicorn

    uvicorn.run(
        "xiaowo_web.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
