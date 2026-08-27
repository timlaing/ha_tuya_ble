# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this integration, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email: [timlaing@users.noreply.github.com](mailto:timlaing@users.noreply.github.com)

Include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgement**: within 48 hours
- **Initial assessment**: within 1 week
- **Fix or mitigation**: depends on severity, but typically within 2 weeks for critical issues

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
