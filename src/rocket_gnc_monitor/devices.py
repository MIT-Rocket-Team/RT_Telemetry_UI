"""Independent bounded serial workers for rocket telemetry/uplink and pointer commands."""

from __future__ import annotations
import queue
import threading
import time
import uuid
import serial
from serial.tools import list_ports
from .protocol import TelemetryDecoder


def serial_device_key(device):
    """macOS tty/cu aliases identify the same physical serial transport."""
    return str(device).replace("/dev/tty.", "/dev/cu.").casefold()


def ports():
    return [
        dict(
            device=p.device,
            description=p.description,
            serial=p.serial_number,
            identity=f"{p.vid}:{p.pid}:{p.serial_number}"
            if p.serial_number
            else p.device.replace("/dev/tty.", "/dev/cu."),
        )
        for p in list_ports.comports()
    ]


class SerialWorker:
    def __init__(self, role, device, generation, emit, raw, baud=115200, on_observation=None):
        if role not in {"telemetry", "uplink", "pointer"}:
            raise ValueError("Unknown serial role")
        self.role, self.device, self.generation = role, device, generation
        self.emit, self.raw, self.baud = emit, raw, baud
        self.on_observation = on_observation
        self.connection_id = None
        self.observation_counter = 0
        self.stop_event = threading.Event()
        self.poll_event = threading.Event()
        self.commands = queue.Queue(maxsize=1 if role == "pointer" else 64)
        self.decoder = TelemetryDecoder(on_accepted=self._accepted) if role == "telemetry" else None
        self.connection = None
        self.thread = threading.Thread(target=self.run, name=f"{role}-serial", daemon=True)
        self.thread.start()

    def send(self, payload, command_id):
        if self.stop_event.is_set():
            raise ValueError("Serial transport is disconnected")
        # Never silently replace manual commands. Tracking sends only after its preceding dispatch.
        self.commands.put_nowait((time.monotonic() + 0.5, bytes(payload), command_id))
        if self.connection:
            try:
                self.connection.cancel_read()
            except (AttributeError, OSError):
                pass

    def stop(self):
        self.stop_event.set()
        if self.connection:
            try:
                self.connection.cancel_read()
            except (AttributeError, OSError):
                pass
        self.thread.join(timeout=1.5)

    def set_polling(self, enabled):
        if self.role != "telemetry":
            raise ValueError("Polling belongs to the ground station")
        if enabled:
            self.poll_event.set()
        else:
            self.poll_event.clear()
            if self.connection:
                try:
                    self.connection.cancel_read()
                except (AttributeError, OSError):
                    pass

    def message(self, kind, data):
        self.emit(self.generation, self.role, kind, data)

    def _accepted(self, sample, raw_frame):
        self.observation_counter += 1
        if self.on_observation is not None:
            context = {
                "connection_id": self.connection_id,
                "observation_counter": self.observation_counter,
                "decoder_gaps": self.decoder.gaps,
                "rejected_packets": self.decoder.rejected,
            }
            try:
                # The sink must enqueue bounded work; it runs before the GUI
                # queue so rendering delays cannot erase reception records.
                self.on_observation(sample, raw_frame, context)
            except Exception as exc:
                # A monitoring/archive problem must not kill the serial link.
                self.message("observation_error", str(exc))

    def run(self):
        try:
            self.connection = serial.Serial(
                port=None,
                baudrate=self.baud,
                timeout=1 if self.role == "telemetry" else 0.1,
                write_timeout=0.2,
            )
            self.connection.port = self.device
            self.connection.open()
            self.connection_id = str(uuid.uuid4())
            self.message("connected", self.device)
            while not self.stop_event.is_set():
                if self.role == "telemetry" and not self.poll_event.is_set() and self.commands.empty():
                    # Keep the port open as in the legacy UI; reading starts only with Start Polling.
                    _ = self.connection.in_waiting
                    self.stop_event.wait(0.05)
                    continue
                while not self.commands.empty():
                    try:
                        expires, payload, command_id = self.commands.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        if time.monotonic() > expires or self.stop_event.is_set():
                            self.message("expired", command_id)
                        else:
                            written = self.connection.write(payload)
                            # RGM1 role 4 remains the rocket command wire stream,
                            # including a base station's dedicated uplink board.
                            self.raw("telemetry_tx" if self.role == "uplink" else self.role + "_tx", payload[:written])
                            if written != len(payload):
                                raise IOError("Incomplete serial write")
                            self.message("sent", command_id)
                if self.role == "telemetry" and not self.poll_event.is_set():
                    continue
                chunk = self.connection.read(min(max(self.connection.in_waiting, 1), 4096))
                received, utc = time.monotonic(), time.time()
                if chunk and (self.role != "telemetry" or self.poll_event.is_set()):
                    if self.role != "uplink":
                        self.raw(self.role + "_rx", chunk)
                    if self.role == "telemetry":
                        for sample in self.decoder.feed(chunk, received=received, utc=utc):
                            self.message("sample", sample)
                        self.message(
                            "stats",
                            {
                                key: getattr(self.decoder, key)
                                for key in ("accepted", "rejected", "discarded", "gaps")
                            },
                        )
                    else:
                        self.message("rx", chunk.hex(" "))
        except Exception as exc:
            self.message("error", str(exc))
        finally:
            if self.connection:
                self.connection.close()
            self.message("disconnected", self.device)
