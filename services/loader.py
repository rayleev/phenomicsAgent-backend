"""Load custom services from services.yaml and register them."""

from pathlib import Path
from typing import Optional

from backend.services.http_service import HttpService
from backend.services.registry import ServiceRegistry

SERVICES_CONFIG_PATH = Path(__file__).resolve().parent.parent / "services.yaml"


def load_services_from_yaml(path: Optional[Path] = None) -> int:
    """Read services.yaml and register each custom service.

    Returns the number of services loaded.
    Args:
        path: Path to services.yaml. Defaults to project root.
    """
    config_path = path or SERVICES_CONFIG_PATH

    if not config_path.exists():
        return 0

    import yaml

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "services" not in data:
        return 0

    registry = ServiceRegistry()
    count = 0

    for svc_name, svc_cfg in data["services"].items():
        name = svc_cfg.get("name", svc_name)
        description = svc_cfg.get("description", "")
        url = svc_cfg.get("url", "")
        method = svc_cfg.get("method", "POST")
        headers = svc_cfg.get("headers", {})
        request_template = svc_cfg.get("request_template", {})
        timeout = svc_cfg.get("timeout", 30.0)

        if not name or not url:
            continue  # Skip invalid entries

        http_svc = HttpService(
            name=name,
            description=description,
            url=url,
            method=method,
            headers=headers,
            request_template=request_template,
            timeout=timeout,
        )
        registry.register(http_svc)
        count += 1

    return count
