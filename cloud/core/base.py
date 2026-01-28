from abc import ABC, abstractmethod


class CloudProvider(ABC):
    @abstractmethod
    def ensure_resources(self) -> None:
        """Ensure provider-specific infrastructure exists."""

    @abstractmethod
    def deploy_app(self) -> None:
        """Deploy application artifacts to the provider."""