from argparse import ArgumentParser

from communication_library import ids
from communication_library.communication_manager import CommunicationManager, TransportType
from communication_library.exceptions import TransportTimeoutError, UnregisteredCallbackError
from communication_library.frame import Frame
from communication_library.tcp_transport import TcpSettings


def on_altitude(frame: Frame):
    print(f"Registered frame received: {frame}")


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", default=3000, type=int)
    cl_args = parser.parse_args()

    cm = CommunicationManager()
    cm.change_transport_type(TransportType.TCP)

    altitude_frame = Frame(
        ids.BoardID.SOFTWARE,
        ids.PriorityID.LOW,
        ids.ActionID.FEED,
        ids.BoardID.ROCKET,
        ids.DeviceID.SENSOR,
        2,
        ids.DataTypeID.FLOAT,
        ids.OperationID.SENSOR.value.READ,
    )
    cm.register_callback(on_altitude, altitude_frame)
    cm.connect(TcpSettings(cl_args.address, cl_args.port))

    relay_open_frame = Frame(
        ids.BoardID.ROCKET,
        ids.PriorityID.LOW,
        ids.ActionID.SERVICE,
        ids.BoardID.SOFTWARE,
        ids.DeviceID.RELAY,
        0,
        ids.DataTypeID.FLOAT,
        ids.OperationID.RELAY.value.OPEN,
        (),
    )
    cm.push(relay_open_frame)
    cm.send()

    servo_open_frame = Frame(
        ids.BoardID.ROCKET,
        ids.PriorityID.LOW,
        ids.ActionID.SERVICE,
        ids.BoardID.SOFTWARE,
        ids.DeviceID.SERVO,
        1,
        ids.DataTypeID.INT16,
        ids.OperationID.SERVO.value.POSITION,
        (0,),
    )
    cm.push(servo_open_frame)
    cm.send()

    try:
        while True:
            try:
                cm.receive()
            except TransportTimeoutError:
                pass
            except UnregisteredCallbackError as e:
                print(f"unregistered frame received: {e.frame}")
    except KeyboardInterrupt:
        pass
    finally:
        cm.disconnect()


if __name__ == "__main__":
    main()
