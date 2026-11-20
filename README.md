# GE Opal Ice Maker — Home Assistant custom integration

This custom integration replaces the ESPHome BLE client with a native Home Assistant integration. It uses Home Assistant's Bluetooth device registry (`bluetooth.async_ble_device_from_address`) and `bleak-retry-connector` for reliable GATT connections. It does not create its own scanner.

## Known protocol

Device observed: `OPAL Bluetooth-1B8735`, address `D8:28:C9:1B:87:35`.

- Advertisement service: `e2779da7-0a82-4be7-b754-31ed3e727253`
- GATT service: `3e6763c5-9429-40cc-909e-bebf8c7487be`
- Make State: `097a2751-ca0d-432f-87b5-7d2f31e45551`
- Ice Bin State: `5bcbf6b1-de80-94b6-0f4b-99fb984707b6`
- Night Mode: `37988f00-ea39-4a2d-9983-afad6535c02e`
- Cleaning Phase: `efe4bd77-0600-47d7-b3f6-dc81af0d9aaf`
- Make Ice control: `79994230-4b04-40cd-85c9-02dd1a8d4dd0`

The ESPHome implementation established that control writes are one byte: `0x01` for on and `0x00` for off. Make State values: 0 = Not Making Ice, 1 = Making Ice, 2 = Out of Water, 3 = Ice Bin Full, 4 = Cleaning. Ice Bin State values: 0 = In, 1 = Out.

Only Make State was positively verified to advertise `read` and `notify` in the user's BlueZ output. This integration checks each characteristic's runtime properties before subscribing or reading. It polls readable state characteristics every 45 seconds as a fallback/consistency check. Cleaning Phase is exposed as a raw numeric value because its enumeration is unknown.

## Entities

- Sensor: Make State (human-readable)
- Sensor: Make State Code (raw numeric diagnostic)
- Sensor: Ice Bin State (In/Out)
- Sensor: Cleaning Phase (raw numeric diagnostic)
- Switch: Night Mode
- Switch: Make Ice (state follows Make State == 1)
- Button: Disconnect Bluetooth (drops the connection and stops auto-reconnecting, freeing the Opal for another client such as the phone app)
- Button: Reconnect Bluetooth (resumes the connection worker and connects immediately)

The two buttons stay available while the device is disconnected. The disconnected state is not persisted: it resets to connected when Home Assistant restarts or the entry is reloaded.

Diagnostic buttons (for when the Opal is bonded to another host, since it can only be paired to one device at a time):

- Button: Unpair (disconnects, stops auto-reconnecting, and removes the Bluetooth bond so another host or the phone app can pair with the Opal)
- Button: Re-pair (unpairs, reconnects, and pairs again with this Home Assistant host, then resumes normal operation)

Both show an error in the UI if the Opal is out of range, cannot be connected, or pairing fails or is unsupported by the adapter/proxy. They reuse the same connect/pair logic as the config flow (`pairing.py`). Put the Opal in pairing mode before using Re-pair.

## Installation

1. Copy `custom_components/ge_opal` into the Home Assistant configuration directory so the final path is `<config>/custom_components/ge_opal/`.
2. Restart Home Assistant.
3. Onboarding: if Home Assistant sees the Opal advertising, a **Discovered** card appears under **Settings → Devices & services**; click **Add** and confirm. Otherwise choose **Add integration → GE Opal Ice Maker**: the flow lists nearby Opals to pick from, with an **Enter address manually** option (also shown automatically when none are found).
4. Confirm the device is visible to a **connectable** Bluetooth adapter managed by Home Assistant.

## Pairing note

This integration verifies the connection during setup: before an entry is created, the config flow connects to the selected Opal, checks the Opal GATT service, and reads Make State. If that read is rejected (usually because the device is not bonded), it calls `pair()` once and retries. If connecting or pairing fails, the form shows an error and no entry is created. Pairing needs a backend that supports it (BlueZ on the HA host); some Bluetooth proxies cannot pair, in which case pair via the host first. After setup, the integration does not pair again on its own.

The user successfully paired/bonded this Opal using BlueZ `bluetoothctl`, with an interactive pairing confirmation. The ESPHome configuration used passkey `000000`, but the Home Assistant integration does not install a custom BlueZ pairing agent, so passkey prompts are not answered automatically. Pair the Opal on the same Linux/BlueZ host that runs Home Assistant if the setup-time pairing attempt fails. Pairing performed on a separate laptop does not create a bond on the Home Assistant host. If Home Assistant runs in HA OS, use its host-level Bluetooth/BlueZ pairing route rather than assuming a shell in another computer shares the same adapter.

The runtime connection worker deliberately does not call `pair()`. If the device is not already bonded and rejects protected GATT operations, pre-pair it through the host's supported Bluetooth tooling first.

When the integration entry is removed, Home Assistant first unloads it (the coordinator disconnects and shuts down), then the integration makes a best-effort attempt to reconnect and call `unpair()` to remove the bond. This only runs if the Opal is currently visible and the Bluetooth backend supports unpairing; any failure is logged and never blocks removal. If it fails, remove the bond manually (`bluetoothctl remove <address>`).

## Developer tools (experimental)

The Opal exposes more commands and status messages than this integration uses (for example scheduled on/off). Experimental services and an extended diagnostics download help reverse-engineer them. They work on the live connection, so the Opal must be connected.

- `ge_opal.snapshot`, `ge_opal.capture_start`, `ge_opal.capture_mark`, `ge_opal.capture_stop` — guided, read-only tools: labelled snapshots with byte/bit diffs, and timestamped capture sessions with notes. See the walkthrough below.

- `ge_opal.probe` — enumerates every GATT service/characteristic (UUID, handle, properties, descriptors), reads every readable characteristic, and optionally listens for notifications on all notifying characteristics for `listen_seconds` (0-120). Read-only. The result is returned (use *Developer tools → Actions* with a response) and logged at INFO in hex.
- `ge_opal.send_raw` — writes raw hex bytes (`characteristic`, `data`, e.g. `01` or `01 00 ff`, max 64 bytes) and captures notifications for `listen_seconds`, plus a readback and the decoded state. **Does nothing unless `allow_write: true`.** Every write is logged at WARNING.
- **Diagnostics download** (Device → Download diagnostics) now includes the cached service layout, the last 200 notifications (hex, timestamped) and the last `probe` result. It performs no GATT I/O itself.

Both services accept an optional `address`, needed only if several Opals are configured. The guided tools below (`snapshot`, `capture_start`/`capture_mark`/`capture_stop`) are read-only and also take `address`.

### Guided reverse-engineering walkthrough

Goal: work out what the Opal's other characteristics mean (e.g. scheduled on/off) with as few risky writes as possible. Record everything in [`docs/protocol.md`](docs/protocol.md).

**Important:** the Opal pairs with one host at a time. The official GE app and Home Assistant cannot both be connected. To change things in the app, press *Disconnect Bluetooth* (or *Unpair*) in Home Assistant, use the app, then *Reconnect Bluetooth* (or *Re-pair*). Changes made on the Opal's own controls (buttons, lid, water) work without the app and are the easiest start.

1. **Map it.** Run `ge_opal.probe` (`listen_seconds: 30`). Note every characteristic, its properties (`read`/`write`/`notify`) and its value. Paste the layout into `docs/protocol.md`.
2. **Baseline.** Leave the Opal idle and run `ge_opal.snapshot` with `label: baseline`.
3. **Change exactly one thing**, then snapshot again with a descriptive label (e.g. `night-mode-on`, `schedule-enabled`). The report (persistent notification, log and response) diffs it against the previous snapshot byte by byte and shows which bits flipped. Undo the change and snapshot (`label: restored`) to confirm the same bytes flip back. One variable per snapshot; repeat for power, night mode, schedule on/off, schedule times, light, and so on.
4. **Capture live changes** for things that are hard to snapshot (transient states, notifications): `ge_opal.capture_start` (`label`), perform an action, immediately call `ge_opal.capture_mark` with a note ("pressed power", "enabled schedule 1 in app"), then `ge_opal.capture_stop`. You get a timestamped timeline of your notes, notifications (hex) and characteristic changes tagged with the latest note, plus a start-to-end diff.
5. **Decode.** Compare against the known format: Make State is one byte (0-4), Night Mode one byte (0/1). For each changed byte decide whether it is an enum, a flag bitmask (look at the XOR bits), a counter, or a time value (try hour/minute bytes, 24h vs minutes-since-midnight, days as a 7-bit mask). Vary the value (e.g. schedule at 06:00 then 07:30) to confirm.
6. **Test candidate writes safely**, only after steps 1-5. Prefer characteristics that the app visibly changed and that advertise `write`. Use `ge_opal.send_raw` with `allow_write: true`, change a single byte at a time, take a `snapshot` before and after, and write the original bytes back to restore. Never write while the Opal is cleaning or busy, and avoid unknown characteristics (including vendor service `e936877a-...`) unless you have seen the app change them.
7. **Record findings** in `docs/protocol.md` with the evidence (labels, hex before/after) and a confidence level.

The diagnostics download also includes stored snapshots and the last capture, so you can attach them to a bug report or share them.

### Capturing the official app's traffic (optional)

For commands the Opal only exposes via writes made by the app, capture the app itself:

1. On an Android phone enable *Developer options → Enable Bluetooth HCI snoop log*, then toggle Bluetooth off and on.
2. Use the GE app: connect, then perform one action at a time (note the time of each).
3. Pull the log (`adb bugreport` or copy `btsnoop_hci.log`) and open it in Wireshark. Filter on `btatt` and look for `Write Request`/`Write Command` (app to Opal) and `Handle Value Notification` (Opal to app). Use the Opal's address (`btatt && bluetooth.addr == D8:28:C9:1B:87:35`).
4. Map the ATT handles to UUIDs using the handle numbers in the `ge_opal.probe` output, then add the observed opcodes/bytes and the action you performed to `docs/protocol.md`. Confirm each one with `snapshot`/`capture` before relying on it.

## Testing checklist

1. Confirm Home Assistant sees the Opal in Bluetooth advertisements and reports it as connectable.
2. Add the integration and inspect logs for a successful GATT connection and service discovery.
3. Confirm Make State and Ice Bin State populate. Cleaning Phase may be an undocumented integer.
4. Confirm Make State notifications update the entity promptly; the 45-second read is a fallback.
5. Test Night Mode on/off and verify the reported state changes.
6. Test Make Ice on/off and verify the Make State sensor transitions. Do not repeatedly toggle while the machine is in a cleaning cycle or otherwise busy.
7. Move the Opal out of range and back to verify availability and reconnect behavior.

## Known limitations / next protocol work

- The user's `attribute-info` output has not yet been collected for Ice Bin State, Night Mode, Cleaning Phase, and Make Ice Control. Runtime flags are therefore checked dynamically. If a control characteristic does not advertise a write property, the integration will reject the write instead of guessing.
- The `0x00002902` CCCD descriptors indicate notification configuration exists, but descriptor presence alone does not prove the corresponding characteristic supports notifications.
- Additional vendor-specific characteristics from service `e936877a-8dd0-faa7-b648-f46acda1f27b` and the other unknown characteristics are intentionally not read or written. Their meanings are not known.
- Pairing/passkey management is host/backend-specific. This component relies on an existing OS bond and does not implement a BlueZ Agent1 service.
- This is an initial community-style custom component, not an official or tested GE/Home Assistant integration. Test the controls carefully.
