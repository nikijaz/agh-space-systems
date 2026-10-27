from enum import Enum, IntEnum, unique

HEADER_ID = 0x05


@unique
class BoardID(IntEnum):
    SOFTWARE = 0x01
    ROCKET = 0x02
    LAST_BOARD = 0x09
    PROXY = 0x1E
    BROADCAST = 0x1F


class DeviceID(IntEnum):
    SERVO = 0x00
    RELAY = 0x01
    SENSOR = 0x02


@unique
class ActionID(IntEnum):
    FEED = 0x00
    SERVICE = 0x01
    ACK = 0x02
    NACK = 0x03


@unique
class DataTypeID(IntEnum):
    NO_DATA = 0x00
    UINT32 = 0x01
    UINT16 = 0x02
    UINT8 = 0x03
    INT32 = 0x04
    INT16 = 0x05
    INT8 = 0x06
    FLOAT = 0x07
    INT16X2 = 0x08
    UINT16INT16 = 0x09


@unique
class PriorityID(IntEnum):
    HIGH = 0x00
    LOW = 0x01


@unique
class _ServoOperationID(IntEnum):
    OPEN = 0x01  # unused, for setting position use POSITION
    CLOSE = 0x02  # unused, for setting position use POSITION
    OPENED_POS = 0x03
    CLOSED_POS = 0x04
    POSITION = 0x05
    DISABLE = 0x06
    RANGE = 0x07


@unique
class _RelayOperationID(IntEnum):
    OPEN = 0x01
    CLOSE = 0x02
    STATUS = 0x03


class _SensorOperationID(IntEnum):
    READ = 0x01


class OperationID(Enum):
    SERVO = _ServoOperationID
    RELAY = _RelayOperationID
    SENSOR = _SensorOperationID


class AckStatus(IntEnum):
    DISABLED = 0
    WAITING = 1
    READY = 2
    SUCCESSFUL = 3
    FAILED = 4


class LogLevel(IntEnum):
    NOTSET = 0
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50
