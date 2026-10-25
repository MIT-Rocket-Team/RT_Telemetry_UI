"""In-process pointer transport and nominal-trajectory rehearsal; never opens a port."""

import math
import queue
import struct
import threading
import time
import numpy as np
from .domain import ecef, finite

VIRTUAL_POINTER_DEVICE = "virtual://antenna-pointer"


class VirtualPointer:
    """Illustrative two-axis slew model, driven by the legacy pointer packets."""

    device = VIRTUAL_POINTER_DEVICE
    azimuth_rate = 90.0
    elevation_rate = 60.0

    def __init__(self, generation, emit, raw):
        self.generation, self.emit, self.raw = generation, emit, raw
        self.stop_event = threading.Event()
        self.commands = queue.Queue(maxsize=1)
        self.pose = (0.0, 0.0)
        self.target = self.pose
        self.emit(generation, "pointer", "connected", self.device)

    def send(self, payload, command_id):
        if self.stop_event.is_set():
            raise ValueError("Virtual pointer is disconnected")
        if len(payload) != 11 or payload[0] != 0xAA or sum(payload[1:10]) % 256 != payload[10]:
            raise ValueError("Invalid virtual pointer packet")
        opcode = payload[1]
        if opcode not in range(6):
            raise ValueError("Unsupported virtual pointer command")
        if opcode == 0:
            az, el = struct.unpack_from("<ff", payload, 2)
            if not finite(az) or not finite(el) or not -90 <= el <= 90:
                raise ValueError("Virtual elevation must be between -90 and 90 degrees")
        self.commands.put_nowait((time.monotonic() + 0.5, bytes(payload), command_id))

    def advance(self, seconds):
        if self.stop_event.is_set():
            return
        try:
            expires, payload, command_id = self.commands.get_nowait()
        except queue.Empty:
            pass
        else:
            if time.monotonic() > expires:
                self.emit(self.generation, "pointer", "expired", command_id)
            else:
                az, el = self.target
                opcode = payload[1]
                if opcode == 0:
                    az, el = struct.unpack_from("<ff", payload, 2)
                elif opcode == 5:
                    az, el = 0.0, 0.0
                else:
                    da, de = {1: (0, 5), 2: (0, -5), 3: (-5, 0), 4: (5, 0)}[opcode]
                    az, el = az + da, el + de
                self.target = (az % 360, max(-90.0, min(90.0, el)))
                self.raw("virtual_pointer_tx", payload)
                self.emit(self.generation, "pointer", "sent", command_id)
        az, el = self.pose
        da = (self.target[0] - az + 180) % 360 - 180
        de = self.target[1] - el
        self.pose = (
            (az + max(-self.azimuth_rate * seconds, min(self.azimuth_rate * seconds, da))) % 360,
            el + max(-self.elevation_rate * seconds, min(self.elevation_rate * seconds, de)),
        )

    def hold(self):
        while not self.commands.empty():
            try:
                self.commands.get_nowait()
            except queue.Empty:
                break
        self.target = self.pose

    def stop(self):
        self.stop_event.set()
        self.hold()


def enu_rotation(origin):
    lat, lon = map(math.radians, origin[:2])
    return np.array(
        [
            [-math.sin(lon), math.cos(lon), 0],
            [-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat)],
            [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)],
        ]
    )


class VirtualFlight:
    """Reference ENU -> ECEF -> antenna ENU, with an independent playback clock."""

    def __init__(self, reference, mission):
        mission.validate()
        if not mission.pointer_site_configured:
            raise ValueError("Set the antenna pointer location in Mission → Configure → Antenna pointer")
        if reference is None or reference.manifest.get("synthetic"):
            raise ValueError("Run OpenRocket or load a georeferenced trajectory in Mission & wind first")
        if reference.manifest.get("altitude_datum") != "launch_relative":
            raise ValueError("Virtual tracking requires a launch-relative ENU trajectory")
        self.reference = reference
        self.origin = (mission.pointer_latitude, mission.pointer_longitude, mission.pointer_altitude)
        launch = reference.manifest["origin"]
        rotation = enu_rotation(self.origin)
        self.offset = rotation @ (np.array(ecef(*launch)) - np.array(ecef(*self.origin)))
        self.rotation = rotation @ enu_rotation(launch).T
        self.mount_position = enu_rotation(launch) @ (np.array(ecef(*self.origin)) - np.array(ecef(*launch)))
        self.start, self.end = map(float, reference.points[[0, -1], 0])
        self.time = self.start
        self.playing = False
        self.speed = 1.0
        self.angles = None
        self.position = None
        self.distance = 0.0
        self.seek(self.start)

    def seek(self, seconds):
        if not finite(seconds):
            raise ValueError("Trajectory time must be finite")
        self.time = max(self.start, min(self.end, seconds))
        self.position = self.reference.at(self.time)
        east, north, up = self.offset + self.rotation @ np.array(self.position)
        horizontal = math.hypot(east, north)
        self.distance = math.hypot(horizontal, up)
        previous_azimuth = self.angles[0] if self.angles else 0.0
        self.angles = (
            (
                math.degrees(math.atan2(east, north)) % 360 if horizontal > 1e-6 else previous_azimuth,
                math.degrees(math.atan2(up, horizontal)),
            )
            if self.distance > 1e-6
            else None
        )

    def advance(self, seconds):
        if self.playing:
            self.seek(self.time + seconds * self.speed)
            if self.time >= self.end:
                self.playing = False
