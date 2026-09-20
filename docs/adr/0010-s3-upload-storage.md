# Private S3 upload storage

Status: accepted, September 20, 2026.

## Context

API and ARQ workers must share uploaded source bytes without a shared application
filesystem. Existing documents reference content-addressed organization-prefixed keys.

## Decision

Use Boto3/Botocore against S3. Local development runs RustFS with a persistent
Docker volume and localhost-only ports 19000/19001. Keep existing object keys and
private bucket access; no public bucket policy or browser credentials. The worker
validates the job organization prefix before reading bounded bytes. Storage
failures are explicit; there is no filesystem fallback for new uploads.

`S3_CREATE_BUCKET=true` enables local bootstrap only. Deployment uses an existing
private bucket and separate credentials supplied to both API and worker. The
legacy upload volume is retained solely for migration/backup, not new writes.

## Consequences

Restart API and worker after changing S3 environment values. Back up the RustFS
volume separately from PostgreSQL. Knowledge deletion remains an audit-preserving
soft deletion, not physical object deletion. The migration script copies and
verifies existing bytes without deleting local backups.

## Alternatives considered

Shared local upload directories were rejected because they couple worker and API
hosts. Direct browser uploads are deferred; existing authenticated upload limits
and queue behavior remain in place.
