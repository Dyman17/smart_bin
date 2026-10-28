from control_panel import ControlPanel, app


def test_panel_starts_in_manual_mode_without_hardware_actuation():
    panel = ControlPanel()
    state = panel.state()

    assert state["mode"] == "manual"
    assert state["hardware_enabled"] is False


def test_manual_servo_commands_are_dry_run_by_default():
    panel = ControlPanel()

    assert panel.manual_move("BIN_1", 45).startswith("DRY_RUN MOVE BIN_1 45")
    assert panel.set_position("BIN_2", "open", 100).startswith("DRY_RUN SET BIN_2 OPEN 100")
    assert panel.state()["servo_angles"]["BIN_2"]["open"] == 100


def test_servo_angle_range_is_validated():
    panel = ControlPanel()

    try:
        panel.manual_move("BIN_1", 181)
    except ValueError as exc:
        assert "0 and 180" in str(exc)
    else:
        raise AssertionError("Out-of-range angle was accepted.")


def test_local_dashboard_and_state_endpoint():
    client = app.test_client()

    page = client.get("/")
    state = client.get("/api/state")

    assert page.status_code == 200
    assert b"HerbiBioS" in page.data
    assert state.status_code == 200
    assert state.get_json()["mode"] in {"manual", "automatic"}


def test_dashboard_marks_manual_actions_as_simulated_by_default():
    client = app.test_client()

    move = client.post("/api/servo/move", json={"bin": "BIN_1", "angle": 45})
    save = client.post(
        "/api/servo/position",
        json={"bin": "BIN_1", "position": "open", "angle": 100},
    )

    assert move.status_code == 200
    assert move.get_json()["physical"] is False
    assert move.get_json()["response"] == "DRY_RUN MOVE BIN_1 45"
    assert save.status_code == 200
    assert save.get_json()["persisted"] is False


def test_camera_source_config_points_to_esp_access_point():
    panel = ControlPanel()

    assert panel.camera.source == "http://192.168.4.1/capture"
