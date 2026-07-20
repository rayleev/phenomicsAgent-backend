"""Generic HTTP service built from YAML configuration."""

import re
from typing import Any

import httpx

from backend.services.base import BaseService, ServiceResult


def _extract_placeholders(template: dict) -> set[str]:
    """Extract {placeholder} variable names from a dict's string values.

    Recursively walks nested dicts and lists.
    """
    placeholders: set[str] = set()
    pattern = re.compile(r"\{(\w+)\}")

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for item in value:
                _walk(item)
        elif isinstance(value, str):
            placeholders.update(pattern.findall(value))

    _walk(template)
    return placeholders


def _resolve_template(template: Any, params: dict[str, Any]) -> Any:
    """Replace {placeholder} tokens in template with actual parameter values."""
    if isinstance(template, dict):
        return {k: _resolve_template(v, params) for k, v in template.items()}
    elif isinstance(template, list):
        return [_resolve_template(item, params) for item in template]
    elif isinstance(template, str):
        for key, value in params.items():
            template = template.replace(f"{{{key}}}", str(value))
        return template
    return template


def _build_parameters_from_template(template: dict) -> dict:
    """Generate a JSON Schema parameters dict from a request template.

    All extracted placeholders become required string properties.
    """
    placeholders = _extract_placeholders(template)
    properties = {}
    for ph in placeholders:
        properties[ph] = {
            "type": "string",
            "description": f"参数 {ph}",
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(placeholders) if placeholders else [],
    }


class HttpService(BaseService):
    """A generic service that makes HTTP requests based on YAML configuration."""

    def __init__(
        self,
        name: str,
        description: str,
        url: str,
        method: str = "POST",
        headers: dict | None = None,
        request_template: dict | None = None,
        timeout: float = 30.0,
    ):
        self._name = name
        self._description = description
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.request_template = request_template or {}
        self._timeout = timeout

        # Build parameters from template placeholders
        if self.request_template:
            self._parameters = _build_parameters_from_template(self.request_template)
        else:
            self._parameters = {
                "type": "object",
                "properties": {},
            }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    async def invoke(self, **kwargs) -> ServiceResult:
        try:
            body = _resolve_template(self.request_template, kwargs)
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method=self.method,
                    url=self.url,
                    json=body,
                    headers=self.headers,
                )
                if resp.is_success:
                    return ServiceResult(
                        success=True,
                        data=resp.json(),
                        status_code=resp.status_code,
                    )
                else:
                    return ServiceResult(
                        success=False,
                        error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                        status_code=resp.status_code,
                    )
        except httpx.TimeoutException:
            return ServiceResult(
                success=False,
                error=f"Service timed out after {self._timeout}s",
            )
        except httpx.RequestError as e:
            return ServiceResult(
                success=False,
                error=f"HTTP request failed: {e}",
            )
