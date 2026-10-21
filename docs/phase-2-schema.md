# Phase 2 storage and privacy schema

This is the approved target schema for the Phase 2 implementation. Identifiers are UUIDs. User-owned rows cascade on account deletion unless noted. Times are UTC `timestamptz`; `users.timezone` is an IANA timezone used for local summaries and quiet hours.

## Tables

- **users** — `id`, unique normalized `email`, `email_verified_at`, `timezone` (IANA, default `UTC`), `created_at`. Registration rejects an existing email. Email alerts are suppressed until verification.
- **user_data_keys** — `user_id` primary/foreign key, wrapped per-user DEK, wrapping nonce/tag, master-key version, created/rotated timestamps. The wrapped DEK is removed during hard deletion.
- **passkeys** — `id`, `user_id`, unique WebAuthn credential ID, public key, signature counter, transports, label, `created_at`, `last_used_at`. Supports multiple credentials per account; creation requires an authenticated session.
- **email_verification_tokens** — `id`, `user_id`, unique token hash, `expires_at`, nullable `consumed_at`, `created_at`. The email contains a single-use link; plaintext token is never stored or logged.
- **recovery_codes** — `id`, `user_id`, unique code hash, `created_at`, nullable `consumed_at`. Generate a set only after passkey re-authentication; display plaintext once and store only salted hashes. Each code can be consumed once.
- **refresh_sessions** — `id`, `user_id`, token-family UUID, refresh-token hash, `expires_at`, `revoked_at`, `replaced_by_id`, created/last-used timestamps. Never store bearer tokens.
- **consents** — `id`, `user_id`, scope, text version and SHA-256, granted/revoked timestamps. Every saved night references the specific active consent used at analysis time.
- **devices** — `id`, `user_id`, source, label, last-seen time, encrypted push endpoint/key where applicable, created/revoked timestamps.
- **nights** — `id`, `user_id`, optional `device_id`, source, `source_validated`, start time, duration, `consent_id`, created time. Exists only for an analysis explicitly opted into persistence. Index `(user_id, started_at)` and `(user_id, source, started_at)`.
- **results** — `id`, unique `night_id`, encrypted JSON chart/result payload, envelope-encryption key version, `model_version`, created time. Keep source metadata on `nights`; keep risk and signal-derived fields inside the encrypted payload.
- **alert_rules** — `id`, `user_id`, rule kind, enabled state, JSON configuration, delivery channels, quiet-hours local start/end, created/updated timestamps. Device-silent rule is stored disabled and cannot be enabled before Phase 3.
- **alerts** — `id`, `user_id`, optional `night_id`, rule kind, encrypted detail, created time, status. Notification text sent externally is generic and contains no score or health values.
- **alert_source_nights** — `(alert_id, night_id)` composite key linking every saved night used to derive an alert; both foreign keys cascade. Deleting any contributing night deletes the alert and its delivery records so derived details cannot outlive their source data.
- **delivery_log** — `id`, `alert_id`, channel, attempted time, status, provider error code. No destination address, provider response body, notification payload, or token.
- **push_subscriptions** — `id`, `user_id`, encrypted Web Push subscription, created/revoked timestamps.
- **audit_events** — `id`, nullable `user_id`, event kind (`login`, `consent_granted`, `consent_revoked`, `export`, `delete`), timestamp, outcome, request ID. No email, health payload, credential, IP address, or token. On account deletion, earlier user-linked events are deleted; retain only the delete event with `user_id=NULL` and no reversible account reference.
- **deletion_tombstones** (separate restricted store) — one-way account reference, deletion timestamp, expiry. It exists only to remove deleted accounts from encrypted backups during restore; contains no email, key, or health data and is destroyed after the 30-day backup window.

## Consent and deletion behavior

Analysis is open to anonymous users. Anonymous analyses and signed-in analyses without `persist_result=true` remain ephemeral; they do not create `nights` or `results`. Persistence requires a signed-in, verified account, `persist_result=true`, and an active `consent_id`. Revoking a consent requires `delete_saved_nights`; `true` deletes the nights and dependent alert details referencing that consent, while `false` keeps already-saved nights under their recorded consent history but prevents future saves using that revoked consent. The UI must ask explicitly.

Hard deletion requires a fresh one-use passkey re-authentication token plus the typed confirmation phrase. It deletes the account, passkeys, sessions, consent, devices, nights/results, rules, alerts, subscriptions, and prior user-linked audit rows. Retain a delete audit event with no user reference. It revokes sessions, cancels queued work, deletes encrypted temporary blobs, and removes Redis job metadata. Logs contain no account identifiers or payloads.

## Encryption key hierarchy and operations

`MASTER_KEY` is supplied by the deployment environment or secret store and is never checked into source control. Generate a random per-user data-encryption key (DEK); encrypt result/chart payloads with authenticated envelope encryption using the DEK, then wrap the DEK with the master key. Store only ciphertext, nonce/tag, wrapped DEK, and key-version metadata. Never reuse a DEK across users.

Rotation: deploy a new master-key version while retaining the old version as read-only; unwrap each DEK with the old key and rewrap with the new key in a resumable, audited migration; verify all rows; then remove the old key only after backups containing old wrapped keys expire. Payloads need not be re-encrypted when only the wrapping key changes. Rotation of a compromised DEK requires decrypting and re-encrypting that user's payloads with a new DEK.

If the master key is lost with no recoverable secret-store version, wrapped DEKs cannot be recovered and encrypted results are permanently unreadable. There is no plaintext fallback or key escrow. Restore procedures must verify key availability before serving data.

## Temporary work, backups, and logs

RQ uses a JSON serializer only; pickle is disabled. Redis runs on a private Docker network with a required password and `appendonly no` / `save ""`. Uploads are never placed in Redis. API workers write encrypted temporary analysis blobs to a dedicated private volume; jobs carry only opaque blob IDs, expire after one hour, and delete blobs on completion or expiry. Ephemeral job/result metadata expires from Redis after 24 hours. Enforce a 100 MB combined upload limit before writing. The temporary volume is not backed up.

Database backups are encrypted and retained for 30 days. A hard delete removes live rows and the user's wrapped DEK immediately. Deletion tombstones are retained separately from database backups for at least the 30-day backup window. Restores happen in quarantine; tombstones are replayed and deleted keys purged before restored data can serve requests. Backups expire and are destroyed at day 30. Redis has no disk persistence; temporary blobs are excluded from backups. Application, proxy, mail, and worker logs must never include emails, uploaded content, result payloads, access/refresh/reauth tokens, WebAuthn assertions, recovery codes, or push subscription values.
