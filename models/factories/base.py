from abc import ABC, abstractmethod
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel


class AbstractModelFactory(ABC):
    """Abstract factory for building chat model instances."""
    @abstractmethod
    async def create(self, model_name: str, *, force_recreate: bool = False, **kwargs) -> Optional[BaseChatModel]:
        """Return a chat model instance or None when unsupported."""
        raise NotImplementedError
