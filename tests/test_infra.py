"""Bootstrap is idempotent and state survives a restart."""
from mm import infra, state
from mm.api import Api


def test_bootstrap_idempotent():
    first = infra.bootstrap()
    second = infra.bootstrap()
    assert second["admin_created"] is False
    assert first["team_id"] == second["team_id"]
    assert first["token"] == second["token"], "persistent token should be reused"
    assert set(second["channels"]) == set(infra.DEFAULT_CHANNELS)


def test_admin_token_works():
    admin = state.load_admin()
    me = Api(admin["token"]).get("/users/me")
    assert "system_admin" in me["roles"]


def test_bot_settings_enabled_by_env():
    admin = state.load_admin()
    cfg = Api(admin["token"]).get("/config")["ServiceSettings"]
    assert cfg["EnableBotAccountCreation"] is True
    assert cfg["EnableUserAccessTokens"] is True
