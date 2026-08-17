"""Azure Functions entry point for the FastAPI application."""

import azure.functions as func

try:
    from app.api import app as fastapi_app
except ModuleNotFoundError:  # Local import from repository root.
    from backend.app.api import app as fastapi_app


app = func.AsgiFunctionApp(
    app=fastapi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)
