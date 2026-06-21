## Ring Stash v1.0.7

### Security

Private Ring camera and doorbell clips now require Home Assistant administrator authentication.

Previously, the clip media endpoint did not require Home Assistant authentication. In certain network configurations, a person able to reach the Home Assistant instance could request stored clips if the filename was known or guessed.

### Changes

- Require Home Assistant authentication for clip media requests
- Restrict clip media access to Home Assistant administrators
- Return `401 Unauthorized` for unauthenticated media requests
- Return `403 Forbidden` for authenticated non-admin media requests
- Load clips and thumbnails through Home Assistant’s authenticated frontend request API
- Restrict the Ring Stash sidebar panel to administrators
- Revoke temporary browser media URLs after use
- Add regression tests for HTTP media authentication and authorisation
- Bump integration version to `1.0.7`

### Upgrade recommendation

All users should upgrade to `v1.0.7` or later.
