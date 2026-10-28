from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Union

from communication_library import ids

Numeric = Union[int, float]


@dataclass(frozen=True, order=True)
class Frame:
    """
    Represents a frame used to exchange information with the rocket.
    :param destination:  device that the frames is sent to
    :param priority:     level of how important the information is
    :param action:       type of action that the frame represents (e.g.
                         service or request)
    :param source:       device that the frame is sent from
    :param device_type:  type of the hardware that the action is connected
                         to (e.g. servo or relay)
    :param device_id:    id number of the device, unique within device type
    :param data_type:    type of data that is being sent (e.g. int32 or float)
    :param operation:    id of the operation performed on the device
    :param payload:      actual information sent with the frame
    """

    destination: int = field(metadata={"bits": 5})
    priority: int = field(metadata={"bits": 2}, hash=False, compare=False)
    action: int = field(metadata={"bits": 4})
    source: int = field(metadata={"bits": 5})
    device_type: int = field(metadata={"bits": 6})
    device_id: int = field(metadata={"bits": 6})
    data_type: int = field(metadata={"bits": 4}, hash=False, compare=False)
    operation: int = field(metadata={"bits": 8})
    payload: tuple[Numeric, ...] = field(metadata={"bits": 32}, hash=False, compare=False, default_factory=tuple)

    def __post_init__(self) -> None:
        for field_name, value in self.as_dict().items():
            if field_name == "payload":
                self._ensure_payload_type(value)
            else:
                self._ensure_value_type(field_name, value)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _ensure_payload_type(self, payload: tuple[Numeric, ...]) -> None:
        error_msg = f"{self} has payload of type {type(payload)}"
        assert isinstance(payload, tuple), error_msg
        zero_padding = (0 for _ in range(self._valid_payload_len - len(payload)))
        object.__setattr__(self, "payload", (*self.payload, *zero_padding))
        error_msg = f"{self} has wrong payload length (expected {self._valid_payload_len})"
        assert self._valid_payload_len == len(self.payload), error_msg

    @property
    def _valid_payload_len(self) -> int:
        return {
            int(ids.DataTypeID.NO_DATA): 0,
            int(ids.DataTypeID.UINT32): 1,
            int(ids.DataTypeID.UINT16): 1,
            int(ids.DataTypeID.UINT8): 1,
            int(ids.DataTypeID.INT32): 1,
            int(ids.DataTypeID.INT16): 1,
            int(ids.DataTypeID.INT8): 1,
            int(ids.DataTypeID.FLOAT): 1,
            int(ids.DataTypeID.INT16X2): 2,
            int(ids.DataTypeID.UINT16INT16): 2,
        }[self.data_type]

    def _ensure_value_type(self, field_name: str, value: int) -> None:
        try:
            object.__setattr__(self, field_name, int(value))
        except (ValueError, TypeError) as err:
            error_msg = f"{self!r} failed to convert {field_name} of type {type(value)}: "
            raise AssertionError(error_msg + str(err))

    @property
    def data(self) -> Numeric | tuple[Numeric, ...]:
        return self.payload[0] if self._valid_payload_len == 1 else self.payload

    @classmethod
    def values_format_str(cls) -> str:
        return "".join("u" + str(f.metadata["bits"]) for f in fields(cls) if f.name != "payload")

    @classmethod
    def payload_format_str(cls, data_type: int) -> str:
        # padding 'p' added to always match 32 bits total
        return {
            int(ids.DataTypeID.NO_DATA): "p32",
            int(ids.DataTypeID.UINT32): "u32",
            int(ids.DataTypeID.UINT16): "u16p16",
            int(ids.DataTypeID.UINT8): "u8p24",
            int(ids.DataTypeID.INT32): "s32",
            int(ids.DataTypeID.INT16): "s16p16",
            int(ids.DataTypeID.INT8): "s8p24",
            int(ids.DataTypeID.FLOAT): "f32",
            int(ids.DataTypeID.INT16X2): "s16s16",
            int(ids.DataTypeID.UINT16INT16): "u16s16",
        }[data_type]

    def as_reversed_frame(self) -> Frame:
        return Frame(
            destination=self.source,
            priority=self.priority,
            action=self.action,
            source=self.destination,
            device_type=self.device_type,
            device_id=self.device_id,
            data_type=self.data_type,
            operation=self.operation,
            payload=self.payload,
        )

    def as_mono_str(self) -> str:
        device_name = ids.DeviceID(self.device_type).name
        return " ".join(
            (
                f"{ids.BoardID(self.destination).name:<9}",
                f"{ids.PriorityID(self.priority).name:<4}",
                f"{ids.ActionID(self.action).name:<8}",
                f"{ids.BoardID(self.source).name:<7}",
                f"{device_name:<9}",
                f"{self.device_id:<2}",
                f"{self.payload_format_str(self.data_type):<7}",
                f"{ids.OperationID[device_name].value(self.operation).name:<15}",
                f"{self.payload}",
            )
        ).lower()

    def __str__(self) -> str:
        device_name = ids.DeviceID(self.device_type).name
        return ", ".join(
            (
                f"Frame({ids.BoardID(self.destination).name}",
                f"{ids.PriorityID(self.priority).name}",
                f"{ids.ActionID(self.action).name}",
                f"{ids.BoardID(self.source).name}",
                f"{device_name}",
                f"{self.device_id}",
                f"{ids.DataTypeID(self.data_type).name}",
                f"{ids.OperationID[device_name].value(self.operation).name}",
                f"{self.payload})",
            )
        ).lower()
