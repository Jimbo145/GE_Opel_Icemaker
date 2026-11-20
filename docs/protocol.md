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
| `e936877a-8dd0-faa7-b648-f46acda1f27b` | Vendor service | Not yet inspected |

## Characteristics

| UUID | Name | Properties | Format | Confidence | Evidence / notes |
| --- | --- | --- | --- | --- | --- |
| `097a2751-ca0d-432f-87b5-7d2f31e45551` | Make State | read, notify | byte 0: 0 idle, 1 making ice, 2 out of water, 3 bin full, 4 cleaning | verified | From the ESPHome implementation |
| `5bcbf6b1-de80-94b6-0f4b-99fb984707b6` | Ice Bin State | read (runtime-checked) | byte 0: 0 in, 1 out | likely | ESPHome |
| `37988f00-ea39-4a2d-9983-afad6535c02e` | Night Mode | read/write (runtime-checked) | byte 0: 0 off, 1 on | likely | ESPHome |
| `efe4bd77-0600-47d7-b3f6-dc81af0d9aaf` | Cleaning Phase | read (runtime-checked) | byte 0: unknown enumeration | guess | Exposed raw |
| `79994230-4b04-40cd-85c9-02dd1a8d4dd0` | Make Ice control | write | byte 0: 1 start, 0 stop | likely | ESPHome |

## Discovered / unknown

Add findings here. Suggested columns: UUID or handle, byte index, bit mask,
meaning, values seen, confidence, capture label.

| UUID / handle | Byte / bits | Meaning | Values seen | Confidence | Capture / evidence |
| --- | --- | --- | --- | --- | --- |
| _e.g. `xxxxxxxx-...`_ | _byte 2, bit 0_ | _schedule 1 enabled_ | _00 off, 01 on_ | _guess_ | _capture `sched-on`, 12:04 UTC_ |

## Schedule on/off (in progress)

Record here the characteristic(s) that change when a schedule is created,
enabled, disabled or edited in the GE app, the byte layout (days bitmask, start
time, end time, enabled flag, ...), and the safe write sequence once found.
