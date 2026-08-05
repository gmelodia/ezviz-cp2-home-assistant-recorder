# EZVIZ CP2 Recorder

See the repository [README](../README.md) for installation, one-time MFA provisioning, API usage and Home Assistant automation examples.

The App writes completed recordings to:

`/media/intrusioni/<name>_cp2.mp4`

Local API endpoints:

- `GET /health`
- `GET /status` with `X-API-Key`
- `POST /capture` with `X-API-Key`
- `POST /recover` with `X-API-Key`
