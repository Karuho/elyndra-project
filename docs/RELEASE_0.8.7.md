# Elyndra 0.8.7-alpha

## Stabilized web UX and isolated multi-account runtime

This release consolidates the 0.8.6 authentication fixes into a versioned architecture. Multiple accounts can register with unique usernames and emails, but each account receives a separate SQLite vault and data/state/cache directories. Legacy data is preserved only for the first account.

The web shell now uses dedicated login/register routes, real account switching, developer-mode authorization, a fixed navigation/account frame, scrollable history, compact search and lazy new-chat persistence. It removes raw model adapter labels and exposes an accurate Local/Online capability control; online access remains disabled.

SQLite advances to schema 48. Network access, remote recovery, telemetry delivery and 2FA activation remain disabled.
