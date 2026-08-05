# EZVIZ CP2 Recorder

See the repository [README](../README.md) for installation, one-time MFA provisioning, API usage and Home Assistant automation examples.

The App writes completed recordings and alarm snapshots to:

- `/media/intrusioni/<name>_cp2.mp4`
- `/media/intrusioni/<name>_cp2.jpg`

`POST /capture` waits until the JPEG snapshot has been saved, then returns `202 Accepted` with both paths:

```json
{
  "snapshot": "/media/intrusioni/<name>_cp2.jpg",
  "output": "/media/intrusioni/<name>_cp2.mp4"
}
```

This allows a Home Assistant automation to pass `response.content.snapshot` directly to `telegram_bot.send_photo` while the video continues recording in the background.

Local API endpoints:

- `GET /health`
- `GET /status` with `X-API-Key`
- `POST /capture` with `X-API-Key`
- `POST /recover` with `X-API-Key`
