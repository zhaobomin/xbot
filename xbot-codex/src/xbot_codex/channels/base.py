from __future__ import annotations

from abc import ABC, abstractmethod

from xbot_codex.events import OutboundMessage


class BaseChannel(ABC):
    name: str = "base"

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        pass
