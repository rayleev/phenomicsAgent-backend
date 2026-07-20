"""Service result data class and abstract service interface."""
from dataclasses import dataclass, field
from typing import Any, Optional
from abc import ABC, abstractmethod


@dataclass
class ServiceResult:
    """Structured result from a service invocation."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: Optional[int] = None


class BaseService(ABC):
    """Abstract base class for all services.

    Subclasses must set name, description, and parameters, and implement invoke().
    """

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
    })

    @abstractmethod
    async def invoke(self, **kwargs) -> ServiceResult:
        """Execute the service with the given keyword arguments.

        Returns a ServiceResult indicating success or failure.
        """
        ...

