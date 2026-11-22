"""Mission invitations identify the publishing Base and carry candidate LAN addresses."""
from urllib.parse import parse_qs, urlencode, urlsplit

from .protocol import endpoint, require, uuid_text


def make_invite(mission, publisher, addresses):
    return f"rtmission://join/{mission}?" + urlencode({"base": publisher, "address": addresses}, doseq=True)


def read_invite(value):
    require(isinstance(value, str) and len(value) <= 4096, "Invalid mission invite")
    parts = urlsplit(value.strip())
    require(parts.scheme == "rtmission" and parts.netloc == "join" and not parts.fragment,
            "Paste an invite copied from the Base Missions window")
    query = parse_qs(parts.query, strict_parsing=True, max_num_fields=17)
    require(set(query) == {"base", "address"} and len(query["base"]) == 1, "Invalid mission invite")
    addresses = list(dict.fromkeys(endpoint(address) for address in query["address"]))
    return uuid_text(parts.path.removeprefix("/")), uuid_text(query["base"][0]), addresses
