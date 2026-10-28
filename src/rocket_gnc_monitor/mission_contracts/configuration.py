"""Versioned drafts with cross-reference validation. Runnable-job validation arrives with the runner."""

import uuid

from .contracts import MAX_BYTES, encode, parse, resource, validate, write_atomic


def validate_draft(draft):
    encode(draft)  # Apply JSON size, number and nesting limits to callers' dictionaries too.
    issues = validate(resource("mission.schema.json"), draft)
    if issues:
        return issues

    def fail(path, code, message):
        issues.append({"path": path, "code": code, "message": message})

    def unique(group):
        values = {}
        for i, item in enumerate(draft[group]):
            if item["id"] in values:
                fail(f"/{group}/{i}/id", "duplicate-id", "Duplicate ID")
            else:
                values[item["id"]] = item
        return values

    def reference(group, key, path):
        if key not in group:
            fail(path, "missing-reference", "Unknown ID " + key)

    sites, vehicles, assets = unique("sites"), unique("vehicleBindings"), unique("assets")
    unique("pointers")
    unique("stationBindings")
    for key in ("launch-site", "launch"):
        if key not in sites or not sites[key]["enabled"]:
            fail("/sites", "required-site", "Enabled " + key + " is required")
    mounts = set()
    for i, pointer in enumerate(draft["pointers"]):
        site = pointer["siteId"]
        reference(sites, site, f"/pointers/{i}/siteId")
        reference(vehicles, pointer["targetVehicleId"], f"/pointers/{i}/targetVehicleId")
        if pointer["enabled"]:
            if site in mounts or (site in sites and not sites[site]["enabled"]):
                fail(f"/pointers/{i}", "pointer-site", "One enabled pointer per enabled Away site")
            mounts.add(site)
    for i, vehicle in enumerate(draft["vehicleBindings"]):
        if "rocketAssetId" in vehicle:
            reference(assets, vehicle["rocketAssetId"], f"/vehicleBindings/{i}/rocketAssetId")
    channels = set()
    for i, stream in enumerate(draft["video"]["streams"]):
        if stream["id"] in channels:
            fail(f"/video/streams/{i}/id", "duplicate-id", "Duplicate stream ID")
        channels.add(stream["id"])
        reference(vehicles, stream["vehicleId"], f"/video/streams/{i}/vehicleId")
        receivers = set()
        for receiver in stream["receivers"]:
            reference(sites, receiver, f"/video/streams/{i}/receivers")
            if receiver in receivers:
                fail(f"/video/streams/{i}/receivers", "duplicate-route", "Duplicate receiver")
            receivers.add(receiver)
    owners = set()
    for i, station in enumerate(draft["stationBindings"]):
        site = station["siteId"]
        reference(sites, site, f"/stationBindings/{i}/siteId")
        if station["owner"] != "observer" and station["role"] == "telem":
            if site in owners:
                fail(f"/stationBindings/{i}", "multiple-owners", "One telemetry command owner per station")
            owners.add(site)
    if int(draft["execution"]["seed"]) > 2**64 - 1:
        fail("/execution/seed", "range", "Seed must fit unsigned 64 bits")
    if int(draft["execution"]["deliveryTailUs"]) > 60_000_000:
        fail("/execution/deliveryTailUs", "range", "Delivery tail cannot exceed 60 seconds")
    return issues


def create_urrg(title="URRG mission"):
    draft = resource("urrg-draft.json")
    draft["identity"].update(missionId=str(uuid.uuid4()), title=title)
    if issues := validate_draft(draft):
        raise ValueError(issues)
    return draft


def load_draft(path):
    with open(path, "rb") as source:
        draft = parse(source.read(MAX_BYTES + 1))
    if issues := validate_draft(draft):
        raise ValueError(issues)
    return draft


def save_draft(path, draft):
    if issues := validate_draft(draft):
        raise ValueError(issues)
    write_atomic(path, draft)
