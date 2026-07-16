"""FastAPI 依赖注入: 从 app.state 取共享的长连接 Store 单例."""

from __future__ import annotations

from fastapi import Request

from src.infra.llm_service.store import LLMServiceStore
from src.persistence.api_key_store import SqliteApiKeyStore
from src.persistence.auth_store import SqliteAuthStore
from src.persistence.http_log_store import HttpLogStore
from src.persistence.memory_store import SqliteMemoryStore


def get_auth_store(request: Request) -> SqliteAuthStore:
    return request.app.state.auth_store


def get_api_key_store(request: Request) -> SqliteApiKeyStore:
    return request.app.state.api_key_store


def get_memory_store(request: Request) -> SqliteMemoryStore:
    return request.app.state.memory_store


def get_http_log_store(request: Request) -> HttpLogStore:
    return request.app.state.http_log_store


def get_llm_service_store(request: Request) -> LLMServiceStore:
    return request.app.state.llm_service_store
