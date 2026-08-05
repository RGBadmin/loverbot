from loverbot.config import Cfg


def test_defaults_on_empty():
    cfg = Cfg({})
    assert cfg.timezone == "Asia/Shanghai"
    assert cfg.min_gap_minutes == 45
    assert cfg.max_silence_hours == 30
    assert cfg.max_unanswered == 3
    assert cfg.heartbeat_minutes == 5
    assert cfg.panel_port == 9700
    assert cfg.imagegen_order == []


def test_missing_required():
    assert len(Cfg({}).missing_required()) == 3
    cfg = Cfg({
        "telegram": {"main_bot_token": "t", "admin_id": "123"},
        "models": {"chat": {"base_url": "https://x/v1", "api_key": "k", "model": "m"}},
    })
    assert cfg.missing_required() == []


def test_model_fallback_to_chat():
    cfg = Cfg({
        "models": {
            "chat": {"base_url": "https://x/v1", "api_key": "k", "model": "big"},
            "light": {"model": "small"},
        }
    })
    light = cfg.model_conf("light")
    assert light["model"] == "small"
    assert light["base_url"] == "https://x/v1"
    assert light["api_key"] == "k"
    # vlm 完全未配置 → 整体回落 chat
    assert cfg.model_conf("vlm")["model"] == "big"


def test_explicit_zero_kept():
    cfg = Cfg({"proactive": {"min_gap_minutes": 0}})
    assert cfg.min_gap_minutes == 0


def test_override():
    cfg = Cfg({})
    cfg.apply_override("proactive.min_gap_minutes", 10)
    assert cfg.min_gap_minutes == 10
