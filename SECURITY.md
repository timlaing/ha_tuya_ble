# Security Policy

## Reporting a vulnerability

Please **do not** report security vulnerabilities in the public issue tracker.
Instead, report vulnerabilities privately through **GitHub's private
vulnerability reporting**, which goes directly to the maintainers:

> **Report a security vulnerability** → **https://github.com/timlaing/ha_tuya_ble/security/advisories**

You can also use **GitHub Security Advisories** from the repository's
**Security** tab (if enabled for this repository).

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### What happens next

- We aim to acknowledge receipt of security reports within a reasonable
  timeframe and to keep you informed as we triage.
- We will coordinate on a fix and release before the issue is publicly
  disclosed wherever possible.
- We ask that reporters allow time for a fix and release before public
  disclosure, and credit the reporter if they wish.

## Scope

This policy covers the `ha_tuya_ble` custom integration. It does not cover:

- Home Assistant core vulnerabilities (report to [Home Assistant Security](https://www.home-assistant.io/security/))
- Tuya cloud service vulnerabilities (report to Tuya directly)
- Physical BLE protocol vulnerabilities (report to the BLE specification body)

## Security Considerations

This integration handles BLE encryption keys and device credentials. Key security properties:

- All BLE communication is encrypted using Tuya's AES-CTR mode with per-device keys
- Encryption keys are stored locally in Home Assistant's `.storage` directory — they are never transmitted after initial key exchange
- The QR code login flow obtains device keys from Tuya's cloud API, but all subsequent communication is local (BLE only)
- No telemetry or device data is sent to any cloud service after setup

## Supported Versions

| Version  | Supported |
| -------- | --------- |
| Latest   | Yes       |
| < Latest | No        |

## Security tooling

This project uses a number of automated checks to help keep the codebase secure:

- GitHub **CodeQL** analysis runs in CI ([`.github/workflows/codeql.yml`](.github/workflows/codeql.yml)).
- **SonarQube Cloud** reports security ratings and vulnerabilities
  (`sonar.projectKey=timlaing_ha_tuya_ble`).
- GitHub **Dependabot** monitors dependency updates and known-vulnerability
  advisories.
- `prek run --all-files` runs hooks including `detect-private-key` and
  `detect-secrets`-class checks to prevent committing secrets.
