"""Service registry — centralized registration and lookup."""

from typing import Optional

from services.base import BaseService


class ServiceRegistry:
    """Singleton registry for all services.

    Services are registered by name and can be looked up at runtime.
    The registry also provides a tool list compatible with LLM Function Calling.
    """

    _instance: Optional["ServiceRegistry"] = None

    def __new__(cls) -> "ServiceRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._services: dict[str, BaseService] = {}
        return cls._instance

    def register(self, service: BaseService) -> None:
        """Register a service instance."""
        if not service.name:
            raise ValueError("Service must have a non-empty name")
        self._services[service.name] = service

    def get(self, name: str) -> Optional[BaseService]:
        """Look up a service by name."""
        return self._services.get(name)

    def list_tools(self) -> list[dict]:
        """Return tool descriptions in OpenAI Function Calling format.

        Each entry has the structure expected by both Anthropic and OpenAI APIs:
          {
            "type": "function",
            "function": {
              "name": "...",
              "description": "...",
              "parameters": {...}
            }
          }
        """
        tools = []
        for svc in self._services.values():
            tools.append({
                "type": "function",
                "function": {
                    "name": svc.name,
                    "description": svc.description,
                    "parameters": svc.parameters,
                },
            })
        return tools

    def clear(self) -> None:
        """Clear all registered services (useful for testing)."""
        self._services.clear()

    @property
    def count(self) -> int:
        return len(self._services)
