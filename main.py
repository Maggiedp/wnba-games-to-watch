#!/usr/bin/env python3
"""Local development entry point for the WNBA Games to Watch API."""

import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("API_PORT", 8000))
    host = os.getenv("API_HOST", "127.0.0.1")

    uvicorn.run(
        "src.api.app:app",
        host=host,
        port=port,
        reload=True,
    )
