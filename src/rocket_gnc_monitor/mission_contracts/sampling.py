"""sha256-counter-v1, shared with Java; never consumes a global random stream."""

import hashlib
import math
import re
import struct

VERSION = "sha256-counter-v1"


def digest(seed, member, participant, phenomenon, model, event, draw):
    if not isinstance(seed, str) or re.fullmatch(r"0|[1-9][0-9]{0,19}", seed) is None or int(seed) >= 2**64:
        raise ValueError("Invalid unsigned seed")
    if any(type(n) is not int or not 0 <= n < 2**63 for n in (member, event, draw)):
        raise ValueError("Invalid sampler counter")
    fields = (VERSION, seed, str(member), participant, phenomenon, model, str(event), str(draw))
    data = bytearray()
    for field in fields:
        if not isinstance(field, str) or not 1 <= len(field) <= 1024:
            raise ValueError("Invalid sampler identity")
        value = field.encode("utf-8")
        data.extend(struct.pack(">I", len(value)))
        data.extend(value)
    return hashlib.sha256(data).digest()


def uniform(seed, member, participant, phenomenon, model, event, draw):
    value = digest(seed, member, participant, phenomenon, model, event, draw)
    n = int.from_bytes(value[:7], "big") >> 4
    return (n + 0.5) / 2**52


def normal(seed, member, participant, phenomenon, model, event, pair):
    a = uniform(seed, member, participant, phenomenon, model, event, pair * 2)
    b = uniform(seed, member, participant, phenomenon, model, event, pair * 2 + 1)
    return math.sqrt(-2 * math.log(a)) * math.cos(2 * math.pi * b)
