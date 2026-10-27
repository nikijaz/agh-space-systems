import struct

import bitstruct
from crccheck.crc import Crc32Mpeg2

from communication_library.exceptions import ChecksumMismatchError, ProtocolError
from communication_library.frame import Frame
from communication_library.ids import HEADER_ID


class GroundStationProtocol:
    """
    AGH Space Systems main ground station protocol for rocket communication.
    """

    HEADER_BYTE_LENGTH = 1
    PAYLOAD_BYTE_LENGTH = 4
    CRC_BYTE_LENGTH = 4

    @classmethod
    def encode(cls, frame: Frame) -> bytes:
        try:
            data = cls._pack(frame)
        except bitstruct.Error as err:
            raise ProtocolError(f"Encoding {frame} to bytes failed:" + str(err))

        data = bytes(cls._reverse_bits(byte) for byte in data)
        crc = cls.calculate_crc(data)
        return data + crc

    @classmethod
    def _pack(cls, frame: Frame) -> bytes:
        values = tuple(v for k, v in frame.as_dict().items() if k != "payload")

        header = bitstruct.pack(f"<u{cls.HEADER_BYTE_LENGTH * 8}", HEADER_ID)
        values = bitstruct.pack("<" + Frame.values_format_str(), *values)
        payload = bitstruct.pack("<" + Frame.payload_format_str(frame.data_type), *frame.payload)
        return header + values + payload

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        data, crc = data[: -cls.CRC_BYTE_LENGTH], data[-cls.CRC_BYTE_LENGTH :]
        if crc != cls.calculate_crc(data):
            raise ChecksumMismatchError

        data = bytes(cls._reverse_bits(byte) for byte in data)
        try:
            return cls._unpack(data)
        except bitstruct.Error as err:
            raise ProtocolError(f"Decoding {data} to frame failed:" + str(err))

    @classmethod
    def _unpack(cls, data: bytes) -> Frame:
        data, payload = data[: -cls.PAYLOAD_BYTE_LENGTH], data[-cls.PAYLOAD_BYTE_LENGTH :]
        _, values = data[: cls.HEADER_BYTE_LENGTH], data[cls.HEADER_BYTE_LENGTH :]

        values = bitstruct.unpack("<" + Frame.values_format_str(), values)
        data_type = Frame(*values).data_type
        payload = bitstruct.unpack("<" + Frame.payload_format_str(data_type), payload)
        return Frame(*values, payload=payload)

    @classmethod
    def calculate_crc(cls, data: bytes, skip_padding: bool = False, return_endianess: str = "little") -> bytes:
        # padding to a multiple of 4 bytes, because we're using 32bit crc
        if not skip_padding:
            data += (4 - (len(data) % 4)) * b"\x00"
        format_str = int(len(data) / 4) * "I"
        big_endian_data = struct.pack(">" + format_str, *struct.unpack(format_str, data))
        return Crc32Mpeg2.calc(big_endian_data).to_bytes(cls.CRC_BYTE_LENGTH, return_endianess)

    @classmethod
    def _reverse_bits(cls, byte: int) -> int:
        # Reversing bit order using int's binary padded string representation
        return int(f"{byte:08b}"[::-1], 2)
