from __future__ import annotations

from collections import deque
from typing import Callable

from communication_library.exceptions import (
    MissingHeaderError,
    TransportError,  # pylint: disable=ungrouped-imports
    UnregisteredCallbackError,
)
from communication_library.frame import Frame  # pylint: disable=ungrouped-imports
from communication_library.ids import HEADER_ID, BoardID, PriorityID
from communication_library.protocol import GroundStationProtocol  # pylint: disable=ungrouped-imports
from communication_library.tcp_transport import TcpTransport  # pylint: disable=ungrouped-imports
from communication_library.transport import TransportInfo, TransportOptions, TransportSettings, TransportType


class CommunicationManager:
    """
    Main communication interface for the Ground Station.
    """

    def __init__(self) -> None:
        self._transport = None
        self._protocol = GroundStationProtocol()
        self._priority_buffer = {int(priority): deque() for priority in PriorityID}
        self._callbacks = {}

    @property
    def transport_info(self) -> TransportInfo:
        """
        Returns info about connection: serial port, baudrate, read and write timeout.
        """
        return self._transport.info

    def change_transport_type(self, transport_type: TransportType):
        if self.is_connected:
            self._transport.close()

        if transport_type == TransportType.TCP:
            self._transport = TcpTransport()

        else:
            raise TransportError(f"Attempted to use non existent transport: {transport_type}")

    @property
    def transport_options(self) -> TransportOptions:
        return self._transport.options()

    @property
    def is_connected(self) -> bool:
        """
        Property returning current connection state.
        """
        if self._transport is None:
            return False

        return self._transport.is_open

    def connect(self, transport_options: TransportSettings, timeout: int = 0, write_timeout: int | None = 1) -> None:
        """
        Opens the communication transport.
        :param transport_options: used to establish underlying transport connection
        :param timeout: read timeout in seconds, None for forever, 0 for non-blocking
        :param write_timeout: write timeout in seconds, same as read timeout
        """
        for queue in self._priority_buffer.values():
            queue.clear()
        self._transport.open(transport_options, timeout, write_timeout)

    def disconnect(self) -> None:
        """
        Closes the communication transport.
        """
        self._transport.close()

    def register_callback(self, callback: Callable, frame: Frame) -> None:
        """
        Registers a Callable as a hook called upon receiving a response.
        :param callback: response hook
        :param frame: frame used as a key for response callback
        """
        if frame.destination == BoardID.BROADCAST:
            callback_keys = self.create_broadcast_callback_keys(frame)
            for key in callback_keys:
                assert key not in self._callbacks
                self._callbacks[key] = callback
        else:
            # frame = frame.as_reversed_frame()
            assert frame not in self._callbacks
            self._callbacks[frame] = callback

    def unregister_callback(self, frame: Frame):
        frame = frame.as_reversed_frame()
        self._callbacks.pop(frame, None)

    def clear_callbacks(self):
        self._callbacks.clear()

    def push(self, frame: Frame) -> None:
        """
        Put the frame in a buffer for sending
        :param frame: frame to add to the queue
        """
        self._priority_buffer[frame.priority].append(frame)

    def pop(self, default=None) -> Frame:
        """
        Pop out first of the buffered frames according to their priority.
        """
        for queue in self._priority_buffer.values():
            if queue:
                return queue.popleft()
        return default

    def send(self) -> Frame:
        """
        Sends first of the queued frames to the hardware.
        """
        frame = self.pop()
        if frame is not None:
            frame_bytes = self._protocol.encode(frame)
            self._transport.write(frame_bytes)
        return frame

    def receive(self) -> Frame:
        """
        Receives some data from the transport, governed by the protocol.
        """
        header = self._transport.read(1)
        if header != bytes([HEADER_ID]):
            raise MissingHeaderError(f"Received byte is not a header: {header}")

        raw_frame = self._transport.read(13)
        frame = self._protocol.decode(header + raw_frame)
        frame_matched = False
        try:
            self._callbacks[frame](frame)
            frame_matched = True
        except KeyError:
            pass

        if not frame_matched:
            raise UnregisteredCallbackError(frame)

        return frame

    def clear_pattern_pre_processors(self):
        self._pattern_pre_processors = []

    def clear_pattern_post_processors(self):
        self._pattern_post_processors = []

    def create_broadcast_callback_keys(self, frame) -> list[Frame]:
        frame_kwargs = frame.as_reversed_frame().as_dict()
        callback_keys = []
        assert frame_kwargs["source"] == BoardID.BROADCAST
        for source in BoardID:
            if source == BoardID.LAST_BOARD:
                break
            if source not in (BoardID.BROADCAST, BoardID.GRAZYNA):
                frame_kwargs["source"] = source
                callback_keys.append(Frame(**frame_kwargs))
        return callback_keys

    @property
    def read_buffer_size(self) -> int:
        return self._transport.read_buffer_size
