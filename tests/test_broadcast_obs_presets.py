import importlib.util
import json
from pathlib import Path


def test_obs_presets_are_credential_free_and_start_on_independent_slate(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_broadcast_obs.py"
    spec = importlib.util.spec_from_file_location("prepare_obs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/standby.png").write_bytes(b"fixture slate")
    module.prepare(tmp_path / "obs", tmp_path / "assets", tmp_path / "recordings")
    collection = json.loads((tmp_path / "obs/scenes/Rocket_Broadcast.json").read_text())
    assert collection["current_program_scene"] == "Emergency slate"
    capture = next(s for s in collection["sources"] if s["id"] == "screen_capture")
    assert capture["settings"]["type"] == 1 and capture["settings"]["window"] == 0
    assert capture["settings"]["show_cursor"] is False
    slate = next(s for s in collection["sources"] if s["id"] == "image_source")
    assert Path(slate["settings"]["file"]).read_bytes() == b"fixture slate"
    assert Path(slate["settings"]["file"]).is_relative_to(tmp_path / "obs")
    for profile in (tmp_path / "obs/profiles").iterdir():
        settings = (profile / "basic.ini").read_text()
        encoder = json.loads((profile / "streamEncoder.json").read_text())
        assert "RecFormat2=mkv" in settings and "FPSCommon=30" in settings
        assert encoder["keyint_sec"] == 2 and encoder["rate_control"] == "CBR"
        assert not (profile / "service.json").exists()
    assert "password" not in json.dumps(collection).lower()
