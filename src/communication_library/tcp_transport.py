from __future__ import annotations

import errno
import re
import select
import socket
from collections import deque

from communication_library.exceptions import ClosedTransportError, TransportError, TransportTimeoutError
from communication_library.transport import Transport, TransportInfo, TransportOptions, TransportSettings


class TcpOptions(TransportOptions):
    def __init__(self):
        self.address: str = "0.0.0.0/0"
        self.port: str = "0 - 65535"


class TcpInfo(TransportInfo):
    def __init__(self, active: bool, transport_type: str, address: str, port: int):
        self.status = "Active" if active else "Inactive"
        self.transport_type = transport_type
        self.address = address
        self.port = port

    def __dict__(self) -> dict:
        return {"Status": self.status, "Type": self.transport_type, "Address": self.address, "Port": self.port}


class TcpSettings(TransportSettings):
    def __init__(self, address: str, port: int):
        self.address = address
        self.port = port

    @classmethod
    def options(cls) -> TcpOptions:
        return TcpOptions()

    def validate(self):
        ipv4_regex = re.compile(r"^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$")
        if re.match(ipv4_regex, self.address) is None:
            raise ValueError(f'Address: "{self.address}" is not a valid IPV4 address')

        if not 65535 >= self.port >= 0:
            raise ValueError(f'Port: "{self.port}" is not between 0 - 65535')


class TcpTransport(Transport):
    def __init__(self):
        self._receive_cache = deque()
        self._send_cache = deque()
        self._write_timeout = 0
        self._read_timeout = 0
        self._address = None
        self._port = None
        self._socket = None
        self._socket_open = False
        self._receive_cache_size = 8192

    @property
    def read_timeout(self) -> float:
        """
        Property for timeout of the socket read action in seconds.
        """
        return self._socket.timeout

    @property
    def write_timeout(self) -> float:
        """
        Property for timeout of the socket write action in seconds.
        """
        return self._socket.timeout

    @classmethod
    def options(cls) -> TcpOptions:
        """
        Options available to supply while establishing a transport connection.
        """
        return TcpSettings.options()

    @property
    def info(self) -> TcpInfo:
        """
        Information regarding current transport state.
        """
        return TcpInfo(active=self.is_open, transport_type=type(self).__name__, address=self._address, port=self._port)

    @property
    def is_open(self) -> bool:
        """
        Property checking if the transport is open.
        :return: True if the transport is open, False otherwise
        """
        return self._socket_open

    def open(self, settings: TcpSettings, read_timeout: float = 0, write_timeout: float | None = 1) -> None:
        """
        Opens socket connection with the given arguments.

        The supplied parameters beside "options" are being ignored
        they were only left here to ensure backwards compatibility
        with code already present in the codebase.

        :param settings: options required to establish a transport connection
        :param read_timeout: read timeout in seconds, None for forever, 0 for non-blocking
        :param write_timeout: write timeout in seconds, same as read timeout
        """
        try:
            address = settings.address
            port = settings.port
        except ValueError:
            raise TransportError("Socket parameters are incorrect")

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect((address, port))
        self._socket.settimeout(0)
        self._socket_open = True
        self._address = address
        self._port = port

    def close(self) -> None:
        """
        Closes the transport.
        """
        if hasattr(self, "_socket"):
            self._socket.close()
        self._socket_open = False

    def write(self, data: bytes) -> None:
        """
        Writes bytes of data to the socket.
        :param data: data bytes to send
        """
        _, writable, _ = select.select([], [self._socket], [], 0)
        if not writable:
            raise ClosedTransportError("Writing to a closed socket")
        writable[0].sendall(data)

    def read(self, number_of_bytes: int = 1) -> bytes:
        """
        Reads bytes of data from the socket. Buffer additional data.
        :param number_of_bytes: number of bytes to be read
        :return: requested number of bytes.
        """

        if not self._socket_open:
            raise ClosedTransportError("Reading from a closed socket")

        # If requested amount of bytes is bigger than max cache size, raise an exception
        if number_of_bytes > self._receive_cache_size:
            raise ValueError(
                f"Requested amount of bytes: {number_of_bytes}, "
                f"exceeds max cache size of: {self._receive_cache_size}. "
                f"This read will never succeed. Please perform a smaller read."
            )

        # If buffer has exact amount of bytes requested or bigger, return immediately skipping transport read
        if number_of_bytes <= len(self._receive_cache):
            return bytes(self._receive_cache.popleft() for _ in range(number_of_bytes))

        # Read as many bytes as possible from transport and return requested amount
        readable, _, _ = select.select([self._socket], [], [], 0)
        if not readable:
            raise TransportTimeoutError("Timeout while reading from socket")
        try:
            available_space = self._receive_cache_size - len(self._receive_cache)

            data = readable[0].recv(available_space)
            self._receive_cache.extend(data)

        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise TransportTimeoutError("Timeout while reading from socket")
            if e.errno == errno.ECONNRESET:
                self._socket_open = False
                raise ClosedTransportError("Reading from a closed socket")
            raise TransportError("Received unexpected error from transport")

        if not data:
            self._socket_open = False
            raise ClosedTransportError("Reading from a closed socket")

        # Timeout if the amount read was still smaller than amount of bytes requested
        if len(self._receive_cache) < number_of_bytes:
            raise TransportTimeoutError("Timeout while reading from socket")

        # Return requested amount of bytes
        return bytes(self._receive_cache.popleft() for _ in range(number_of_bytes))

    @property
    def read_buffer_size(self) -> int:
        """
        Returns the number of bytes in the read buffer.
        """
        return len(self._receive_cache)
