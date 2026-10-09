# Contributing to WebAgent

Thanks for helping improve WebAgent.

## Good first contributions

- browser reliability regressions
- clearer error messages
- provider/model metadata fixes
- accessibility/semantic browser actions
- artifact formatting
- documentation
- UI polish
- focused regression tests
- safety and human-in-the-loop improvements

## Development setup

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pytest
python -m playwright install chromium
```

Run the test suite:

```powershell
python -m pytest -q
```

Launch the app:

```powershell
python -m webagent.app
```

## Pull requests

Please keep pull requests focused. A good PR should:

1. explain the user-visible problem;
2. describe the approach;
3. include or update tests where practical;
4. avoid unrelated formatting/refactors;
5. update README/release notes when behavior changes; and
6. preserve existing safety boundaries unless the PR intentionally strengthens them.

## Browser-agent safety

Do not add features whose purpose is to bypass CAPTCHA, anti-bot controls, access controls, or website security mechanisms.

Consequential final actions should remain human-controlled. Changes affecting purchases, messages, deletions, submissions, credentials, authentication, or other irreversible actions deserve explicit review and tests.

## Secrets and private data

Never commit:

- API keys or tokens
- `.env` files containing secrets
- browser profiles/cookies
- real diagnostic archives containing user data
- private workspace artifacts
- credentials copied from AgentSmith/GroqVM

Use synthetic fixtures in tests.

## Style

The existing codebase favors compact Python and straightforward behavior over framework-heavy abstractions. Match the surrounding module where reasonable, and prioritize readability in new or substantially changed code.

## Reporting bugs

Use the GitHub bug-report template and include:

- WebAgent version
- Windows/Python version
- routing mode
- provider/model involved (without keys)
- steps to reproduce
- relevant **redacted** diagnostics

For security-sensitive issues, follow `SECURITY.md` instead of posting exploit details publicly.
