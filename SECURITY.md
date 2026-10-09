# Security Policy

WebAgent controls a real browser and may interact with authenticated websites, so security reports are taken seriously.

## Supported version

Security fixes are expected to target the latest release on the default branch.

## Reporting a vulnerability

Please **do not publish working exploit details, secrets, credentials, private browser data, or sensitive diagnostic archives in a public issue**.

Preferred reporting path:

1. Use GitHub's **Private vulnerability reporting** feature if it is enabled for the repository.
2. If private reporting is not available, open a minimal public issue stating that you have a security report and need a private contact channel. Do not include exploit details in that issue.

A useful report includes the affected version, impact, reproduction conditions, and a minimal proof of concept using synthetic/non-sensitive data.

## Security boundaries

WebAgent intentionally:

- does not attempt to solve or bypass CAPTCHA/human-verification challenges;
- pauses the autonomous loop during detected human verification;
- restricts file tools to the configured workspace root;
- avoids displaying/storing raw provider keys in normal WebAgent configuration;
- redacts secrets from diagnostic logs where implemented; and
- instructs the browser agent to stop before consequential final actions.

These safeguards reduce risk but do not make browser automation infallible. Users should supervise high-impact workflows and protect browser profiles, workspace data, API keys, and diagnostic exports.
