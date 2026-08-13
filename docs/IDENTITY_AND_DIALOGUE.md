# Identity and dialogue foundation

`0.8.6-alpha` introduces one active local account per Elyndra installation and separates four concepts that must not be conflated:

1. operating-system user;
2. local account username;
3. preferred conversational name;
4. Elyndra developer identity.

Only the authenticated local profile may influence how Elyndra addresses the current person. The developer identity is never added to ordinary prompts. If no preferred name exists, Elyndra addresses the person as “tú”. Blank optional identity fields are omitted rather than inferred.

## Registration

Required fields are username, email, password, password confirmation and birth date. Registration rejects minors and invalid dates. Passwords must contain 8–64 characters, at least one letter, one number and one special character; uppercase is recommended but not mandatory. Telemetry and developer mode are optional checkboxes and default to disabled.

## Sessions and security

Passwords use Argon2id. Session tokens are random and only their SHA-256 hashes are stored. Changing the password revokes all sessions. The schema includes disabled 2FA factors for a future release, but no 2FA secret is generated or accepted now.

## Profile and privacy

Preferred name, pronouns, sex, gender identity and sexual orientation are independent fields. Undefined values are not shown to the model. Birthday greetings, developer mode and telemetry can be changed later. Telemetry has no delivery implementation in this release and its preview excludes all conversational and sensitive content.

## Encrypted local export

CLI and web can produce an encrypted SQLite backup using Scrypt and AES-256-GCM. The export passphrase is not stored. Remote recovery is represented only as disabled settings for future compatibility.

## Dialogue continuity

A clarification stores a short-lived, bounded set of options by chat ID. The next terse answer is compared only against those options. The state is consumed once and expires after 30 minutes. It is not a general chain-of-thought store.

Capability-help questions are handled locally from known features. Elyndra must explain the Personal workspace or concrete CLI command and must not invent that a developer or third person must operate the assistant.
