from __future__ import annotations

import pytest

from preop_triage.settings import ENV_FORCE_LLM, ENV_NO_CACHE, ENV_OFFLINE, Settings

FLAGS = [
    (ENV_OFFLINE, "offline"),
    (ENV_NO_CACHE, "no_cache"),
    (ENV_FORCE_LLM, "force_llm"),
]


def test_defaults_all_off_when_unset() -> None:
    assert Settings.from_env() == Settings(offline=False, no_cache=False, force_llm=False)


@pytest.mark.parametrize(("env_name", "attr"), FLAGS)
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES", "Yes", " yes "])
def test_truthy_values_enable_flag(
    monkeypatch: pytest.MonkeyPatch, env_name: str, attr: str, value: str
) -> None:
    monkeypatch.setenv(env_name, value)

    settings = Settings.from_env()

    assert getattr(settings, attr) is True
    others = [a for _, a in FLAGS if a != attr]
    assert all(getattr(settings, a) is False for a in others)


@pytest.mark.parametrize(("env_name", "attr"), FLAGS)
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "2", "y", "enabled"])
def test_other_values_leave_flag_off(
    monkeypatch: pytest.MonkeyPatch, env_name: str, attr: str, value: str
) -> None:
    monkeypatch.setenv(env_name, value)

    assert getattr(Settings.from_env(), attr) is False


def test_explicit_mapping_is_used_instead_of_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_OFFLINE, "1")

    settings = Settings.from_env({ENV_NO_CACHE: "yes"})

    assert settings == Settings(offline=False, no_cache=True, force_llm=False)


def test_settings_are_immutable() -> None:
    with pytest.raises(AttributeError):
        Settings().offline = True  # type: ignore[misc]
