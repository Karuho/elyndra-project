from __future__ import annotations

from elyndra.application import ElyndraApplication
from elyndra.languages import detect_language, parse_language_change
from elyndra.models import LanguageConfig, update_interaction_language
from elyndra.paths import ElyndraPaths


def test_detects_major_scripts_without_model() -> None:
    assert detect_language("你好，这是一个测试").code == "zh"
    assert detect_language("これはテストです").code == "ja"
    assert detect_language("안녕하세요").code == "ko"
    assert detect_language("مرحبا بالعالم").code == "ar"
    assert detect_language("Привет, это тест").code == "ru"


def test_detects_common_latin_languages() -> None:
    assert detect_language("¿Cómo está el sistema y la memoria?").code == "es"
    assert detect_language("How is the system and the memory?").code == "en"
    assert detect_language("Comment est le système et la mémoire ?").code == "fr"


def test_parses_language_change_in_multiple_languages() -> None:
    assert parse_language_change("Cambia a español") == "es"
    assert parse_language_change("Switch to English") == "en"
    assert parse_language_change("切换到西班牙语") == "es"
    assert parse_language_change("スペイン語に切り替えて") == "es"
    assert parse_language_change("¿Por qué el idioma español es importante?") is None


def test_language_preference_round_trip(isolated_home: ElyndraPaths) -> None:
    update_interaction_language(isolated_home, "en")
    fixed = LanguageConfig.load(isolated_home)
    assert fixed.interaction_mode == "fixed"
    assert fixed.preferred_language == "en"

    update_interaction_language(isolated_home, "auto")
    automatic = LanguageConfig.load(isolated_home)
    assert automatic.interaction_mode == "auto"
    assert automatic.preferred_language == "en"


def test_assistant_changes_language_from_chinese_command(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    result = app.ask("切换到西班牙语")

    assert result.ok is True
    config = LanguageConfig.load(isolated_home)
    assert config.interaction_mode == "fixed"
    assert config.preferred_language == "es"
