"""Lazy application state shared by routes."""

from __future__ import annotations

import threading

from fastapi import FastAPI, Request

from schemashift.ai import make_advisor
from schemashift.api.db.session import create_all, make_engine, make_session_factory
from schemashift.api.runs import AppState
from schemashift.api.settings import Settings

_lock = threading.Lock()


def build_state(settings: Settings) -> AppState:
    engine = make_engine(settings.database_url)
    if settings.db_auto_create:
        create_all(engine)
    advisor = make_advisor(settings.anthropic_api_key, settings.llm_model)
    return AppState(settings, engine, make_session_factory(engine), advisor)


def get_state(request: Request) -> AppState:
    app: FastAPI = request.app
    state: AppState | None = getattr(app.state, "services", None)
    if state is None:
        with _lock:
            state = getattr(app.state, "services", None)
            if state is None:
                state = build_state(app.state.settings)
                app.state.services = state
    return state
