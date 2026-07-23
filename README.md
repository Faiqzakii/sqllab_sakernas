# Superset SQL Lab Platform

Local web application for batched Superset SQL Lab extraction, merged dataset snapshots, anomaly rule execution, and identity-centric operational findings.

## Browser runtime (Camoufox)

SQL Lab auth/UI uses Camoufox (stealth Firefox), not stock Playwright Chromium.

```bat
pip install -e .
camoufox fetch
```

Persistent profile directory: `.camoufox-profile/` (gitignored).
Override with env `SUPERSET_BROWSER_PROFILE_DIR`.

