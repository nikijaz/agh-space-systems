import asyncio
import logging
import sys
from argparse import ArgumentParser
from collections import deque

from communication_library.ids import HEADER_ID
from communication_library.protocol import GroundStationProtocol


class ProxyClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.send_queue = deque()
        self._should_stop = False

    @property
    def should_stop(self):
        return self._should_stop

    def get_key(self):
        return self.reader

    def stop(self):
        self._should_stop = True

    def push_data_to_send(self, data):
        self.send_queue.append(data)

    def get_data_to_send(self):
        return self.send_queue.popleft()

    async def write(self, data):
        self.writer.write(data)
        await self.writer.drain()

    async def readexactly(self, amount):
        return await self.reader.readexactly(amount)


class Proxy:
    def __init__(self, name):
        self.name = name
        self.protocol = GroundStationProtocol()
        self.tcp_address = None
        self.tcp_port = None
        self.mirror_frames = False
        self.clients = {}
        self.setup_loggers()
        self._logger = logging.getLogger(self.name)
        self._send_queue = deque()
        self._external_receive_queue = deque()
        self._external_listeners: list[Proxy] = []

    def push_data_to_send(self, data):
        self._send_queue.append(data)

    def get_data_to_send(self):
        return self._send_queue.popleft()

    def push_external_data_to_forward(self, data):
        return self._external_receive_queue.append(data)

    def get_external_data_to_forward(self):
        return self._external_receive_queue.popleft()

    def register_external_listener(self, listener):
        self._external_listeners.append(listener)

    def setup_loggers(self):
        logger_main = logging.getLogger(self.name)
        logger_main.setLevel(logging.DEBUG)

        fmt = f"[%(asctime)s] [%(levelname)s] [{self.name.upper()}] %(message)s"
        log_formatter = logging.Formatter(fmt=fmt)

        # log_file_path = Path(sys.argv[0]).resolve().parent
        # while log_file_path.name != 'rocket_ground_station':
        #    log_file_path = log_file_path.parent
        # log_file_path = log_file_path.joinpath('logs')
        # Path(log_file_path).mkdir(parents=True, exist_ok=True)
        # file_handler = logging.FileHandler(
        #    join(str(log_file_path),
        #         f'Proxy_{datetime.now().strftime("%Y_%m_%d_%H_%M_%S")}.log'))

        console_handler = logging.StreamHandler(sys.stdout)

        # file_handler.setFormatter(log_formatter)
        console_handler.setFormatter(log_formatter)
        # logger_main.addHandler(file_handler)
        logger_main.addHandler(console_handler)

    def add_client(self, reader, writer: asyncio.StreamWriter):
        client = ProxyClient(reader, writer)
        self.clients.update({client.get_key(): client})
        self._logger.info("Added new client")
        return client

    def remove_client(self, client):
        key = client.get_key()
        if key in self.clients:
            client.stop()
            self.clients.pop(key)
            self._logger.info("Removed client")

    def set_tcp_server_options(self, address, port):
        self.tcp_address = address
        self.tcp_port = port
        self._logger.info(f"Server listen tcp socket set to {self.tcp_address}:{self.tcp_port}")

    def set_frame_mirroring(self, state):
        self.mirror_frames = state
        self._logger.info(f"Frame mirroring set to: {self.mirror_frames}")

    # Handle receiving data from ground station and forwarding it to clients
    async def handle_station_receive(self):
        while True:
            if not self._external_receive_queue:
                await asyncio.sleep(0)
                continue

            data = self.get_external_data_to_forward()

            clients_to_drop = []
            for client in self.clients.values():
                try:
                    client.push_data_to_send(data)
                except ConnectionResetError:
                    clients_to_drop.append(client)
                    continue
            for client in clients_to_drop:
                self.remove_client(client)
            await asyncio.sleep(0)

    async def handle_station_send(self):
        while True:
            if not self._send_queue:
                await asyncio.sleep(0)
                continue

            data = self.get_data_to_send()

            for listener in self._external_listeners:
                listener.push_external_data_to_forward(data)

    # Handle receiving data from client and send it to ground station
    async def handle_client_receive(self, client):
        while not client.should_stop:
            try:
                header = await client.readexactly(1)
                if header != bytes([HEADER_ID]):
                    self._logger.info("missing header")
                    await asyncio.sleep(0)
                    continue
                raw_data = await client.readexactly(13)
            except ConnectionResetError:
                break
            except ConnectionAbortedError:
                self._logger.info("Client disconnected")
                break
            except asyncio.IncompleteReadError:
                break

            self.push_data_to_send(header + raw_data)

            if self.mirror_frames:
                for remote_client in self.clients.values():
                    if client == remote_client:
                        continue
                    remote_client.push_data_to_send(header + raw_data)

        self.remove_client(client)

    # Handle sending data from ground station to client
    async def handle_client_send(self, client: ProxyClient):
        while not client.should_stop:
            if not client.send_queue:
                await asyncio.sleep(0)
                continue

            data = client.get_data_to_send()

            try:
                await client.write(data)
            except ConnectionResetError:
                break

        self.remove_client(client)

    # Handle new TCP client
    async def handle_new_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client = self.add_client(reader, writer)
        asyncio.create_task(self.handle_client_receive(client))
        asyncio.create_task(self.handle_client_send(client))

    async def serve(self):
        server = await asyncio.start_server(self.handle_new_client, self.tcp_address, self.tcp_port)
        asyncio.create_task(self.handle_station_receive())
        asyncio.create_task(self.handle_station_send())
        self._logger.info(f"Listening for tcp connections on socket: {self.tcp_address}:{self.tcp_port}")
        async with server:
            await server.serve_forever()


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--tcp-address", default="127.0.0.1")
    parser.add_argument("--tcp-port", default=3000)
    cl_args = parser.parse_args()

    software_proxy = Proxy(name="software")
    software_proxy.set_tcp_server_options(cl_args.tcp_address, int(cl_args.tcp_port))
    software_proxy.set_frame_mirroring(True)

    hardware_proxy = Proxy(name="hardware")
    hardware_proxy.set_tcp_server_options(cl_args.tcp_address, int(cl_args.tcp_port) + 1)
    hardware_proxy.set_frame_mirroring(False)

    software_proxy.register_external_listener(hardware_proxy)
    hardware_proxy.register_external_listener(software_proxy)

    async def run_proxy():
        await asyncio.gather(software_proxy.serve(), hardware_proxy.serve())

    asyncio.run(run_proxy())


if __name__ == "__main__":
    main()
