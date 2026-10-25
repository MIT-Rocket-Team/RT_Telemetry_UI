"""Team launch-pad and station positions; MGRS presets contain no elevation."""

from types import MappingProxyType

from .domain import Mission
from .location import decode_mgrs


URRG_LAUNCH_MGRS = "18TUN2061530290"
URRG_STATION_MGRS = MappingProxyType({
    "base": "18TUN2063730181",
    "away1": "18TUN2177730106",
    "away2": "18TUN2291333237",
    "away3": "18TUN2015031101",
    "away4": "18TUN2259229678",
})


def mission_for_station(profile):
    """Create a new startup mission; opening a saved mission remains authoritative."""
    mission = Mission(name=f"{profile.vehicle_label} Launch")
    if profile.launch_site == "urrg":
        launch = decode_mgrs(URRG_LAUNCH_MGRS)
        mission.latitude, mission.longitude = launch.latitude, launch.longitude
        mission.launch_location_format = "mgrs"
        mission.launch_location_code = launch.code
        mission.launch_site_name = "URRG"
        mission.site_configured = True
        if profile.station != "base":
            station = decode_mgrs(URRG_STATION_MGRS[profile.station])
            mission.pointer_latitude, mission.pointer_longitude = station.latitude, station.longitude
            mission.pointer_location_format = "mgrs"
            mission.pointer_location_code = station.code
            mission.pointer_site_name = f"URRG:{profile.station}"
            mission.pointer_site_configured = True
    return mission.validate()
