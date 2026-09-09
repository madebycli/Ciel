from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceSpec:
    id: str
    description: str
    permission_sensitive: bool = False


SERVICES = {
    spec.id: spec
    for spec in (
        ServiceSpec("chat", "Conversation and model access"),
        ServiceSpec("voice", "Speech output and push-to-talk input"),
        ServiceSpec("memory", "Character-aware long-term memory"),
        ServiceSpec("desktop", "Launch apps, open files and inspect desktop state", True),
        ServiceSpec("screen", "Wayland portal based screen capture", True),
        ServiceSpec("reminders", "Local scheduled reminders"),
        ServiceSpec("overlay", "Wayland layer-shell overlay"),
        ServiceSpec("web", "HTTP/HTTPS browsing tools", True),
    )
}


def validate_services(service_ids: tuple[str, ...]) -> tuple[ServiceSpec, ...]:
    unknown = [service_id for service_id in service_ids if service_id not in SERVICES]
    if unknown:
        raise ValueError(f"Unknown character services: {', '.join(unknown)}")
    return tuple(SERVICES[service_id] for service_id in service_ids)
