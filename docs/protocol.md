# Opal protocol notes (experimental)

Living record of what is known about the GE Opal BLE protocol. Add a row for
every characteristic, value or field you investigate, and record how confident
you are. Keep raw evidence (hex before/after, what you did) so findings can be
re-checked.

Confidence: **verified** (reproduced, behaviour confirmed), **likely**
(consistent across several captures), **guess** (single observation).

## Services

| Service UUID | Role | Notes |
| --- | --- | --- |
| `e2779da7-0a82-4be7-b754-31ed3e727253` | Advertisement | Used for discovery matching |
| `3e6763c5-9429-40cc-909e-bebf8c7487be` | Main GATT service | Contains the characteristics below |
| `e936877a-8dd0-faa7-b648-f46acda1f27b` | Vendor service | Not referenced by the official Android app (likely OTA/DFU); not inspected |

## Source: official app static analysis

Findings below come from static analysis of the official Android app
"Ice Maker" 1.0.0.1 (package `com.ge.opal`, FirstBuild; the decompiler output
is obfuscated, class names are those in the APK). The app is plain Java (no
Flutter/RN), so the logic is directly readable. Nothing here has been verified
against hardware unless marked **verified**.

App class map: `com.ge.opal.e.a` is the Opal data model (UUID dispatch table and
read/notify lists), `c.c.a.a.b` is the BLE manager (scan, connect, queued
read/write/notify, `createBond`), `com.ge.newui.ui.schedule.ScheduleFragment`
and `com.ge.opal.opal.d` build the schedule payload, `com.ge.opal.opal.c` and
`ProductDashboardFragment` write the control bytes. `com.ge.opal.e.b` (UUIDs
`E0BA615A...`, `B98F1B81...`, etc.) is the unrelated Paragon cooktop/probe.

## Characteristics

All values are accessed through `ByteBuffer.wrap(data)`; the first byte is the
value unless noted. App constant names are in quotes.

| UUID | App name | Dir | Format | Confidence | Source / notes |
| --- | --- | --- | --- | --- | --- |
| `097a2751-ca0d-432f-87b5-7d2f31e45551` | `OPAL_OP_STATE` | read, notify | byte: 0 not making, 1 making ice, 2 out of water, 3 bin full, 4 cleaning (app `com.ge.opal.a.a` maps 0-4 to status strings; other values show blank) | verified | `e/a.a`, `com.ge.opal.a` |
| `79994230-4b04-40cd-85c9-02dd1a8d4dd0` | `OPAL_OP_MODE` | read, notify, write | byte: 1 = start making ice, 0 = stop. Read back updates the button label (0 shows "start", 1 shows "stop"). 1-byte write | likely | `opal/c$a`, `ProductDashboardFragment.onClick` |
| `37988f00-ea39-4a2d-9983-afad6535c02e` | `OPAL_LIGHT` | read, notify, write | byte 0/1, 1-byte write. The app calls it the light; the integration exposes it as "Night Mode" | likely | `opal/c$c`, `ProductDashboardFragment.onCheckChanged` |
| `b45163b3-1092-4725-95dc-1a43ac4a9b88` | `OPAL_ENABLE_DISABLE_SCHEDULE` | read, notify, write | byte: 1 = schedule enabled, 0 = disabled. 1-byte write. **Implemented** as the "Schedule Enabled" switch | likely | `ScheduleFragment.onCheckChanged`, `opal/c$b` |
| `9e1ae873-cb5e-4485-9884-5c5a3ad60e47` | `OPAL_SET_SCHEDULE` | read, notify, write | 24 bytes, see below | likely (layout), uncertain (day bit order) | `ScheduleFragment.q0/n0`, `opal/d` |
| `efe4bd77-0600-47d7-b3f6-dc81af0d9aaf` | `OPAL_CLEAN_CYCLE` | read, notify | byte, stored but not interpreted or written by the app. Not seen to be writable; no clean-start command in the app | guess | `e/a.a` (handler is a no-op) |
| `5bcbf6b1-de80-94b6-0f4b-99fb984707b6` | `OPAL_ERROR_CHAR` (shown as ice bin) | read, notify | byte: 0 = bin in, non-zero = bin out (Product Info "Ice Bin" label). The constant name suggests it may carry more error codes | likely | `ProductInfoFragment.a`, `e/a.d(byte)` |
| `bd205030-b5ce-4847-b78d-83bff1450a6b` | `OPAL_TEMPERATURE_CHAR` | read, notify | 1 byte shown raw in Product Info as the temperature value (unit unknown). **Not yet exposed by the integration** | likely | `ProductInfoFragment` |
| `cf88a5b6-6687-4f14-8e21-bb9e78a40ecc` | `OPAL_FIRMWARE_VERSION` | read | 4 bytes `a.b.c.d` (Opal firmware) | likely | `e/a.a`, `e/a.v()` compares to bundled image `01.07.05.10` (`GE8868_01070510_PPR5.bin.ota`) |
| `318db1f5-67f1-119b-6a41-1eeca0c744ce` | `OPAL_OTA_BT_VERSION` | read | 6 bytes; bytes 2-5 are the BLE module version (bundled image `01.04.00.00`, `opal_ble.bin.signed.01.04.00.00.ota`) | likely | `e/a.a`, `e/a.t()` |
| `14ff6dfb-36fa-4456-927d-759e1a9a8446`, `4fb34ab1-6207-e5a0-484f-e24a7f638fff` | unnamed | read list only | In the Opal read-on-connect list but never decoded for the Opal (the handler logs "NOT UPDATING"). Likely OTA/DFU related | unknown | `e/a.d()` |

The app's notify subscription list for the Opal (`e/a.e()`) is Make State,
OP_MODE, LIGHT, ENABLE_DISABLE_SCHEDULE and SET_SCHEDULE. The read-on-connect
list (`e/a.d()`) is Make State, OP_MODE, LIGHT, `14FF6DFB`, Ice Bin and
`4FB34AB1`. Temperature, firmware and OTA versions are read by the
product/detail screens.

### Schedule payload (`9e1ae873`)

- Exactly **24 bytes**, one per hour of the day (the app's time-grid has 24
  slots; byte index = slot index, assumed 0 = 00:00-01:00).
- Each byte is a **day bitmask**: bit set = Opal should be on during that hour
  on that day. Only bits 0-6 are used; bit 7 is always written as 0.
- Writing is a plain 1-byte-per-hour full overwrite (no opcode, no checksum);
  the app writes the whole array each time a slot is toggled and on leaving
  the screen, and caches it locally.
- **Day bit order is inconsistent in the app.** The read path (`n0`) treats bit
  0 as Sunday, 1 as Monday ... 6 as Saturday (Java `Calendar` order). The write
  path (`q0`) assigns bits by the sort order of Android view resource IDs in a
  sparse array, which is probably Friday, Monday, Saturday, Sunday, Thursday,
  Tuesday, Wednesday. Treat the order as **unknown until tested** with
  `ge_opal.snapshot` (enable a single day/hour in the app and diff).
- Exact semantics of "on" (making ice vs. powered) are not documented; test.
- Whether the Opal's own clock needs to be set for the schedule to work is not
  visible in the app (no time-sync write was found).

## Pairing and discovery (app behaviour)

- Scan is a classic `startLeScan` filtered **by advertised device name containing
  `OPAL Bluetooth`** (`addproduct/d`, `q0`). The integration also matches the
  advertisement service UUID and an `OPAL*` name prefix.
- Bonding uses the stock `BluetoothDevice.createBond()` (Just Works, no PIN) when
  the bond state is `BOND_NONE`; there is no app-level passkey or custom auth
  handshake. After bonding the app connects with `connectGatt` and enables
  notifications by writing the CCCD (`00002902-...`).
- The app queues GATT operations one at a time (read/write/notify operation
  queue), consistent with the integration's write lock.
- The app code does not itself unbond; the one-host-at-a-time limit is the
  device's behaviour.

## OTA firmware

The APK bundles two OTA images (`assets/image/`): the main controller image
`GE8868_01070510_PPR5.bin.ota` (1.7.5.16) and the BLE module image
`opal_ble.bin.signed.01.04.00.00.ota`. The OTA transport characteristics are
not mapped here. **Do not attempt OTA writes**; a bad write can brick the
device.

## Still unknown

- Day bit order and hour-slot origin of the schedule payload (above).
- Meaning of `efe4bd77` (clean cycle) values and whether it can be triggered.
- Meaning of `14ff6dfb`, `4fb34ab1`, and whether `5bcbf6b1` carries error codes.
- Temperature unit/scale and whether it is the ice-maker or water temperature.
- Whether a schedule write requires the schedule to be enabled first, and
  whether notifications on `9e1ae873` fire when the schedule changes on-device.

## Discovered / unknown (your findings)

Add rows from your own captures here.

| UUID / handle | Byte / bits | Meaning | Values seen | Confidence | Capture / evidence |
| --- | --- | --- | --- | --- | --- |
| _e.g. `9e1ae873-...`_ | _byte 6, bit 0_ | _Sunday 06:00-07:00 on_ | _00 off, 01 on_ | _guess_ | _capture `sched-sun-6`, 12:04 UTC_ |


