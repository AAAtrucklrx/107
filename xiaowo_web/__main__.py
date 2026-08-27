"""Run the Web API with the repository's declared Uvicorn dependency."""

from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    uvicorn.run("xiaowo_web.main:app", host="127.0.0.1", port=8000, reload=False)
