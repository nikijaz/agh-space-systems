from abc import ABC, abstractmethod
from enum import IntEnum


class TransportType(IntEnum):
    SERIAL = 0
    TCP = 1
    WEBSOCKET = 2


class TransportOptions(ABC):
    pass


class TransportInfo(ABC):
    @abstractmethod
    def __dict__(self) -> dict:
        pass


class TransportSettings(ABC):
    @classmethod
    @abstractmethod
    def options(cls) -> TransportOptions:
        pass

    @abstractmethod
    def validate(self):
        pass


class Transport(ABC):
    @property
    @abstractmethod
    def read_timeout(self) -> float:
        pass

    @property
    @abstractmethod
    def write_timeout(self) -> float:
        pass

    @classmethod
    @abstractmethod
    def options(cls) -> TransportOptions:
        pass

    @property
    @abstractmethod
    def info(self) -> TransportInfo:
        pass

    @property
    @abstractmethod
    def is_open(self) -> bool:
        pass

    @abstractmethod
    def open(self, settings: TransportSettings, read_timeout: float) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def write(self, data: bytes) -> None:
        pass

    @abstractmethod
    def read(self, number_of_bytes: int) -> bytes:
        pass

    @property
    @abstractmethod
    def read_buffer_size(self) -> int:
        pass
