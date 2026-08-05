# EZVIZ CP2 Home Assistant Recorder

Experimental Home Assistant App that records an **EZVIZ CP2 smart door viewer** on demand through the EZVIZ cloud VTM stream, decrypts the HEVC payload and saves a standard MP4 file under `/media/intrusioni`.

It was developed and tested with:

- model `CS-CP2-R100-6E2WB-GR`;
- firmware `V5.4.0 build 250918`;
- Home Assistant OS on `aarch64`;
- `pyezvizapi 1.0.5.0`.

The project uses an unofficial cloud API and may stop working after an EZVIZ service, firmware or API change. It is not affiliated with EZVIZ.

## What it does

The App exposes a small authenticated HTTP API:

- `GET /health` — unauthenticated health check;
- `GET /status` — current or last recording status;
- `POST /capture` — queue a CP2 snapshot and MP4 recording;
- `POST /recover` — remux the most recent preserved `_failed.ts` clip.

A recording request performs these steps:

1. accepts the request immediately with HTTP `202`;
2. triggers a cloud snapshot to wake the battery-powered CP2;
3. saves the JPEG to `/media/intrusioni/<name>_cp2.jpg`;
4. opens the EZVIZ VTM stream;
5. decrypts the encrypted HEVC payload using the cached camera media key;
6. validates the stream with FFprobe;
7. remuxes it to MP4 without re-encoding;
8. saves it to `/media/intrusioni/<name>_cp2.mp4`.

The snapshot and recording are asynchronous. Poll `GET /status`; when `state` becomes `recording` or `completed`, the path in `snapshot` is ready for Home Assistant or Telegram.

This is a recorder, not a permanent live-stream proxy. That design is intentional because the CP2 sleeps to preserve battery and its cloud stream did not prove reliable as a continuous HTTP feed.

## Installation

1. In Home Assistant open **Settings → Apps → App store**.
2. Open **Repositories** and add:

   `https://github.com/gmelodia/ezviz-cp2-home-assistant-recorder`

3. Install **EZVIZ CP2 Recorder**.
4. Configure at least:

```yaml
username: "your-ezviz-account"
password: "your-ezviz-password"
serial: "your-camera-serial"
region: "apiieu.ezvizlife.com"
api_key: "use-a-long-random-local-api-key"
channel: 1
default_duration: 60
decrypt_codec: "hevc"
wake_snapshot: true
request_mfa_code: false
mfa_code: ""
debug: false
```

Do not expose TCP port `8099` to the Internet.

## One-time media-key setup

The video media key is separate from the normal EZVIZ account token. The App stores both only in its private Home Assistant configuration directory.

1. Enter the EZVIZ username, password and serial.
2. Set `request_mfa_code: true`, save and start the App once.
3. Read the log: EZVIZ should send a one-time verification code to the masked contact shown there.
4. Set `request_mfa_code: false`, enter the received value in `mfa_code`, save and restart immediately.
5. When the log confirms that the media key was saved, clear `mfa_code` and restart.

The App creates:

- `/config/ezviz_token.json` — account session token;
- `/config/cp2_media_key.txt` — camera video-decryption key.

Never publish, paste or back up these files to an untrusted location. Deleting the token normally causes a new login. Deleting the media-key file requires the one-time MFA procedure again.

## API examples

Start a 60-second recording:

```bash
curl -X POST \
  -H 'X-API-Key: YOUR_LOCAL_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"duration":60,"name":"front_door_20260805_210646"}' \
  http://HOME_ASSISTANT_IP:8099/capture
```

The endpoint returns `202 Accepted` immediately. At that point the returned snapshot path is reserved but the JPEG may not exist yet.

Check the result:

```bash
curl -H 'X-API-Key: YOUR_LOCAL_API_KEY' \
  http://HOME_ASSISTANT_IP:8099/status
```

When the snapshot is ready:

```json
{
  "ok": true,
  "state": "recording",
  "snapshot": "/media/intrusioni/front_door_20260805_210646_cp2.jpg",
  "output": null,
  "error": null
}
```

A completed recording contains:

```json
{
  "ok": true,
  "state": "completed",
  "snapshot": "/media/intrusioni/front_door_20260805_210646_cp2.jpg",
  "output": "/media/intrusioni/front_door_20260805_210646_cp2.mp4",
  "error": null
}
```

Only one recording or recovery job can run at a time. Durations are limited to 5–120 seconds.

## Home Assistant automation

A complete `rest_command` and automation example is available in [`examples/home-assistant.yaml`](examples/home-assistant.yaml). It submits the capture request, polls `/status` until the CP2 snapshot is ready, sends that JPEG through Telegram, and records an indoor camera in parallel.

Home Assistant `rest_command` defaults to a 10-second timeout. The recorder therefore never waits for the sleeping CP2 before returning `202`; the potentially slow wakeup happens in the background.

## Troubleshooting

- **`401 unauthorized`** — the `X-API-Key` header does not match the App's `api_key`.
- **`409 capture already running`** — wait for the current job to finish.
- **`flusso HEVC non decifrato correttamente`** — verify the cached media key; try `decrypt_codec: hevc-encrypted-header` only if `hevc` fails.
- **A `_cp2_failed.ts` file remains** — call `POST /recover`, then inspect `GET /status`.
- **The device times out** — close the EZVIZ mobile live view and retry; only one cloud media session may be available.
- **Telegram cannot read the JPEG** — add `/media` to `homeassistant.allowlist_external_dirs`.

## Security notes

- The API is plain HTTP intended only for a trusted LAN.
- Use a long random `api_key` and firewall port `8099` from untrusted networks.
- Debug logs can contain serial numbers, private network information and temporary signed URLs.
- Never commit `ezviz_token.json` or `cp2_media_key.txt`.

## License

MIT. See [LICENSE](LICENSE).
