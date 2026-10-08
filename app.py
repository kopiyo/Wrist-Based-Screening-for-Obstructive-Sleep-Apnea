"""Compatibility entry point for the Phase 1 FastAPI service.

Run the React interface separately with ``cd apps/web && npm run dev`` or use
Docker Compose to start both services together.
"""
import uvicorn


if __name__ == "__main__":
    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000)
