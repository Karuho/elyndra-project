# Language support

Elyndra separates language policy from language-model capability.

## Interaction modes

- `auto`: detect the probable input language and request a reply in that language.
- `fixed`: always request replies in the owner's selected language.

The setting is private and stored in `~/.config/elyndra/language.toml`.

```toml
[interaction]
mode = "auto"
preferred_language = "es"
```

## Local detection

The core detector has no external dependency and does not call a model. It recognizes major writing
systems for Arabic, Chinese, Devanagari, Hebrew, Japanese, Korean, Cyrillic and Thai. For Latin text,
it uses conservative function-word and character hints for Spanish, English, Portuguese, French,
German, Italian and Vietnamese.

Detection is intentionally described as probable rather than authoritative. Short names, code,
URLs and mixed-language messages may fall back to the configured preferred language.

## Commands

```bash
elyndra language status
elyndra language detect "This is a test"
elyndra language set auto
elyndra language set es
elyndra translate --to es "This is a local assistant."
```

Natural-language switch commands are handled deterministically before model inference. Initial
examples include:

```text
Cambia a inglés
Switch to Spanish
切换到西班牙语
スペイン語に切り替えて
```

## Model capability

A configured model may support more or fewer languages than Elyndra can identify. Elyndra records
and enforces the owner's language preference, but translation and generation quality still depend on
the active local or explicitly selected remote model.

## Speech roadmap

Listening is not part of the text model. A future speech adapter will be loaded on demand, require
visible microphone permission, produce a transcript locally, and then pass that transcript through
the same language-policy layer.
