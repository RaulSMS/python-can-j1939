from enum import Enum
import threading
import time
import j1939

class DMState(Enum):
    IDLE = 1
    REQUEST_STARTED = 2
    WAIT_RESPONSE = 3
    WAIT_QUERY = 4
    SERVER_CLEANUP = 5


class MemoryAccess:
    def __init__(self, ca: j1939.ControllerApplication) -> None:
        """
        Makes an overarching Memory access class

        :param ca: Controller Application
        """
        self._ca = ca
        self.query = j1939.Dm14Query(ca)
        self.server = j1939.DM14Server(ca)
        self.proceed = False
        self._ca.subscribe(self._listen_for_dm14)
        self.state = DMState.IDLE
        self.seed_security = False
        self._notify_query_received = None
        self._proceed_function = None

        self._job_thread_end = threading.Event()
        self._job_thread = threading.Thread(target=self._servicer, name='j1939.memory_access servicer_thread')
        # A thread can be flagged as a "daemon thread". The significance of
        # this flag is that the entire Python program exits when only daemon
        # threads are left.
        self._job_thread.daemon = True
        self._job_thread.start()

    def __del__(self):
        self._job_thread_end.set()
        if self._job_thread.is_alive():
            self._job_thread.join()

    def _servicer(self):
        """
        Job thread to service memory access requests
        """
        while not self._job_thread_end.is_set():
            if (self.state == DMState.WAIT_RESPONSE) and self.proceed:
                self.proceed = False
                if self._notify_query_received is not None:
                    self._notify_query_received()  # notify incoming request
            time.sleep(0.001)  # Add a small delay to yield control to other threads


    def _handle_error(self, priority: int, pgn: int, sa: int, timestamp: int, data: bytearray, error_code: int) -> None:
        """
        Handles errors by resetting the state and unsubscribing from DM14 messages

        :param priority: Priority of the message
        :param pgn: Parameter Group Number of the message
        :param sa: Source Address of the message
        :param timestamp: Timestamp of the message
        :param data: Data of the PDU
        :param error_code: Error code to be set
        """
        self.server.error = error_code
        self.server.set_busy(True)
        self.server.parse_dm14(
            priority, pgn, sa, timestamp, data
        )
        self.server.set_busy(False)
        self.reset()

    def _listen_for_dm14(
        self, priority: int, pgn: int, sa: int, timestamp: int, data: bytearray
    ) -> None:
        """
        Listens for dm14 messages and passes them to the appropriate function

        :param priority: Priority of the message
        :param pgn: Parameter Group Number of the message
        :param sa: Source Address of the message
        :param timestamp: Timestamp of the message
        :param data: Data of the PDU
        """
        if pgn == j1939.ParameterGroupNumber.PGN.DM14:
            match self.state:
                case DMState.IDLE:
                    if self.server.state.value == DMState.IDLE.value:
                        self.state = DMState.REQUEST_STARTED
                        self.server.parse_dm14(priority, pgn, sa, timestamp, data)
                        if not self.seed_security:
                            self.state = DMState.WAIT_RESPONSE
                            self._ca.unsubscribe(self._listen_for_dm14)
                            if self._proceed_function is not None:
                                self.proceed = self._proceed_function(
                                    self.server.command,
                                    int.from_bytes(
                                        bytes=self.server.address,
                                        byteorder="little",
                                        signed=False,
                                    ),
                                    self.server.pointer_type,
                                    self.server.length,
                                    self.server.object_count,
                                    0xFFFF,  # placeholder for key
                                    self.server.sa,
                                    self.server.access_level,
                                    0x0,  # placeholder for seed
                                )  # call proceed function and pass in basic parameters
                                if not self.proceed:
                                    self._handle_error(priority, pgn, sa, timestamp, data, 0x100)
                            else:
                                self.proceed = True  # no security, so always proceed

                case DMState.REQUEST_STARTED:
                    self.server.parse_dm14(priority, pgn, sa, timestamp, data)
                    if self.server.state == j1939.ResponseState.SEND_PROCEED:
                        self.state = DMState.WAIT_RESPONSE
                        if self.seed_security:
                            if self.server.verify_key(
                                self.server.seed, self.server.key
                            ):
                                if self._proceed_function is not None:
                                    self.proceed = self._proceed_function(
                                        self.server.command,
                                        int.from_bytes(
                                            bytes=self.server.address,
                                            byteorder="little",
                                            signed=False,
                                        ),
                                        self.server.pointer_type,
                                        self.server.length,
                                        self.server.object_count,
                                        self.server.key,
                                        self.server.sa,
                                        self.server.access_level,
                                        self.server.seed,
                                    )  # call proceed function and pass in basic parameters
                                    if not self.proceed:
                                        self._handle_error(priority, pgn, sa, timestamp, data, 0x100)
                                else:
                                    self.proceed = True  # no proceed function, so always proceed
                            else:
                                self._handle_error(priority, pgn, sa, timestamp, data, 0x1003)

                case DMState.WAIT_QUERY:
                    self.server.set_busy(True)
                    self.server.parse_dm14(priority, pgn, sa, timestamp, data)
                    self.server.set_busy(False)

                case DMState.SERVER_CLEANUP:
                    self.state = DMState.IDLE
                case _:
                    pass
        
    def respond(
        self,
        proceed: bool,
        data: list = None,
        error: int = 0xFFFFFF,
        edcp: int = 0xFF,
        max_timeout: int = 3,
    ) -> list:
        """
        Responds with requested data and error code, if applicable, to a read request

        :param bool proceed: whether the operation is good to proceed
        :param list data: data to be sent to device
        :param int error: error code to be sent to device
        :param int edcp: value for edcp extension
        :param int max_timeout: max timeout for transaction
        """
        if data is None:
            data = []
        
        if self.state is not DMState.WAIT_RESPONSE:
            return data
        
        self.proceed = False
        self._ca.unsubscribe(self._listen_for_dm14)
        return_data = self.server.respond(proceed, data, error, edcp, max_timeout)
        self.state = DMState.SERVER_CLEANUP if self.server.state.value != DMState.IDLE.value else DMState.IDLE
        self._ca.subscribe(self._listen_for_dm14)
        return return_data

    def read(
        self,
        dest_address: int,
        direct: int,
        address: int,
        object_count: int,
        object_byte_size: int = 1,
        signed: bool = False,
        return_raw_bytes: bool = False,
        max_timeout: int = 1,
    ) -> list:
        """
        Make a dm14 read Query

        :param int dest_address: destination address of the message
        :param int direct: direct address of the message
        :param int address: address of the message
        :param int object_count: number of objects to be read
        :param int object_byte_size: size of each object in bytes
        :param bool signed: whether the data is signed
        :param bool return_raw_bytes: whether to return raw bytes or values
        :param int max_timeout: max timeout for transaction
        """
        if self.state == DMState.IDLE:
            self.state = DMState.WAIT_QUERY
            self.address = dest_address
            data = self.query.read(
                dest_address,
                direct,
                address,
                object_count,
                object_byte_size,
                signed,
                return_raw_bytes,
                max_timeout,
            )
            self.reset()
            return data
        else:
            self.reset()
            raise RuntimeWarning("Process already Running")

    def write(
        self,
        dest_address: int,
        direct: int,
        address: int,
        values: list,
        object_byte_size: int = 1,
        max_timeout: int = 1,
    ) -> None:
        """
        Send a write query to dest_address, requesting to write values at address

        :param int dest_address: destination address of the message
        :param int direct: direct address of the message
        :param int address: address of the message
        :param list values: values to be written
        :param int object_byte_size: size of each object in bytes
        :param int max_timeout: max timeout for transaction
        """
        if self.state == DMState.IDLE:
            self.state = DMState.WAIT_QUERY
            self.address = dest_address
            self.query.write(
                dest_address, direct, address, values, object_byte_size, max_timeout
            )
            self.reset()

    def set_seed_generator(self, seed_generator: callable) -> None:
        """
        Sets seed generator function to use
        :param seed_generator: seed generator function
        """
        self.server.set_seed_generator(seed_generator)

    def set_seed_key_algorithm(self, algorithm: callable) -> None:
        """
        Sets seed-key algorithm to be used for key generation

        :param callable algorithm: seed-key algorithm
        """
        self.seed_security = True
        self.query.set_seed_key_algorithm(algorithm)
        self.server.set_seed_key_algorithm(algorithm)

    def set_verify_key(self, verify_key: callable) -> None:
        """
        Sets verify key function to be used for verifying the key

        :param callable verify_key: verify key function
        """
        self.server.set_verify_key(verify_key)

    def set_notify(self, notify: callable) -> None:
        """
        Sets notify function to be used for notifying the user of memory accesses

        :param callable notify: notify function
        """
        self._notify_query_received = notify

    def set_proceed(self, proceed: callable) -> None:
        """
        Sets proceed function to determine if a memory query is valid or not

        :param callable proceed: proceed function
        """
        self._proceed_function = proceed

    def reset(self) -> None:
        """
        Resets both server and query to remove transaction specific data
        """
        self.state = DMState.IDLE
        self._ca.unsubscribe(self._listen_for_dm14)
        self._ca.subscribe(self._listen_for_dm14)
        self.server.reset_server()
        self.query.reset_query()
        self.proceed = False
