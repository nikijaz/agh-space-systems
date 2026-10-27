"""AGH Space Systems rocket communication library.

Provides the frame model, protocol codec, device identifiers and the
high-level :class:`CommunicationManager` used to talk to the rocket
hardware over a transport (TCP).
"""

from communication_library import ids
from communication_library.communication_manager import CommunicationManager
from communication_library.exceptions import (
    ChecksumMismatchError,
    ClosedTransportError,
    CommunicationError,
    MissingHeaderError,
    ProtocolError,
    TransportError,
    TransportNotFoundError,
    TransportTimeoutError,
    UnregisteredCallbackError,
)
from communication_library.frame import Frame
from communication_library.ids import ActionID, BoardID, DataTypeID, DeviceID, OperationID, PriorityID
from communication_library.protocol import GroundStationProtocol
from communication_library.tcp_transport import TcpInfo, TcpOptions, TcpSettings, TcpTransport
from communication_library.transport import (
    Transport,
    TransportInfo,
    TransportOptions,
    TransportSettings,
    TransportType,
)

__all__ = [
    "ActionID",
    "BoardID",
    "ChecksumMismatchError",
    "ClosedTransportError",
    "CommunicationError",
    "CommunicationManager",
    "DataTypeID",
    "DeviceID",
    "Frame",
    "GroundStationProtocol",
    "MissingHeaderError",
    "OperationID",
    "PriorityID",
    "ProtocolError",
    "TcpInfo",
    "TcpOptions",
    "TcpSettings",
    "TcpTransport",
    "Transport",
    "TransportError",
    "TransportInfo",
    "TransportNotFoundError",
    "TransportOptions",
    "TransportSettings",
    "TransportTimeoutError",
    "TransportType",
    "UnregisteredCallbackError",
    "ids",
]
