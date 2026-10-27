class CommunicationError(Exception):
    """Base class for communication exceptions"""


class TransportError(CommunicationError):
    """Base class for all errors related to transport"""


class ProtocolError(CommunicationError):
    """Base class for all errors realted to protocol"""


class ClosedTransportError(TransportError):
    """Called when trying to write or read from a closed transport"""


class TransportTimeoutError(TransportError):
    """Called when reading from transport takes too much time"""


class TransportNotFoundError(TransportError):
    """Called when a transport of given specification does not exist"""


class ChecksumMismatchError(CommunicationError):
    """Raised when calculated checksum doesn't match the received frame"""


class MissingHeaderError(CommunicationError):
    """Raised when the first received byte is not the frame header"""


class UnregisteredCallbackError(CommunicationError):
    """Raised when a callback for the received frame doesn't exist"""

    def __init__(self, frame, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frame = frame

    def __str__(self):
        return f"Unregistered callback for frame: {self.frame}"
