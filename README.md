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

## Installation

1. Copy `custom_components/ge_opal` into the Home Assistant configuration directory so the final path is `<config>/custom_components/ge_opal/`.
2. Restart Home Assistant.
3. Onboarding: if Home Assistant sees the Opal advertising, a **Discovered** card appears under **Settings → Devices & services**; click **Add** and confirm. Otherwise choose **Add integration → GE Opal Ice Maker**: the flow lists nearby Opals to pick from, with an **Enter address manually** option (also shown automatically when none are found).
4. Confirm the device is visible to a **connectable** Bluetooth adapter managed by Home Assistant.

## Pairing note

The user successfully paired/bonded this Opal using BlueZ `bluetoothctl`, with an interactive pairing confirmation. The ESPHome configuration used passkey `000000`, but the Home Assistant integration does not install a custom BlueZ pairing agent or force a pairing operation. Pair the Opal on the same Linux/BlueZ host that runs Home Assistant if pairing is required. Pairing performed on a separate laptop does not create a bond on the Home Assistant host. If Home Assistant runs in HA OS, use its host-level Bluetooth/BlueZ pairing route rather than assuming a shell in another computer shares the same adapter.

This implementation deliberately does not call `pair()` on setup. This avoids silently initiating an interactive pairing operation from Home Assistant, which cannot reliably answer a passkey prompt across all supported Bluetooth backends. If the device is not already bonded and rejects protected GATT operations, pre-pair it through the host's supported Bluetooth tooling first.

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
