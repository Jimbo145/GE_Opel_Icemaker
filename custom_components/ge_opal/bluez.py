"""Pair through the BlueZ D-Bus API with our own pairing agent.

bluetoothctl succeeds where a bare ``Device1.Pair`` call fails because
bluetoothctl registers a pairing agent, and the Opal needs the host to answer
a passkey / confirmation prompt (a plain BlueZ install in Home Assistant has no
agent, so the prompt is rejected). This module registers a temporary agent that
answers those prompts, then pairs and trusts the device.

No ``from __future__ import annotations`` here: dbus_fast reads the D-Bus
signatures from the method annotations.
"""

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/org/ge_opal/agent"
STATIC_PASSKEY = 0  # the Opal uses passkey 000000 in the owner's ESPHome setup


def _make_agent(events: list[str]):
    from dbus_fast.service import ServiceInterface, method

    class OpalAgent(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            events.append("Release")

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
            events.append("RequestPinCode")
            return "000000"

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821
            events.append(f"DisplayPinCode {pincode}")

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
            events.append("RequestPasskey")
            return STATIC_PASSKEY

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: F821
            events.append(f"DisplayPasskey {passkey:06d}")

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821
            events.append(f"RequestConfirmation {passkey:06d}")

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: F821
            events.append("RequestAuthorization")

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821
            events.append(f"AuthorizeService {uuid}")

        @method()
        def Cancel(self):
            events.append("Cancel")

    return OpalAgent()


async def async_remove_device_via_dbus(address: str) -> dict[str, Any]:
    """Remove the device (and any stale bond) from BlueZ via Adapter1.RemoveDevice."""
    result: dict[str, Any] = {"ok": False}
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            async def _iface(path: str, name: str):
                intro = await bus.introspect("org.bluez", path)
                return bus.get_proxy_object("org.bluez", path, intro).get_interface(name)

            managed = await (
                await _iface("/", "org.freedesktop.DBus.ObjectManager")
            ).call_get_managed_objects()
            for path, ifaces in managed.items():
                dev = ifaces.get("org.bluez.Device1")
                if dev and str(dev["Address"].value).upper() == address:
                    adapter = await _iface(str(dev["Adapter"].value), "org.bluez.Adapter1")
                    await adapter.call_remove_device(path)
                    result["ok"] = True
                    result["paired_was"] = bool(dev["Paired"].value)
                    break
            else:
                result["error"] = "device not known to BlueZ"
        finally:
            bus.disconnect()
    except Exception as err:
        result["error"] = f"{type(err).__name__}: {err}"
    return result


async def async_pair_via_dbus(address: str, timeout: float = 45.0) -> dict[str, Any]:
    """Pair with an agent registered; return a diagnostics dict (``ok`` key)."""
    result: dict[str, Any] = {"ok": False, "agent_events": []}
    try:
        from dbus_fast import BusType, Variant
        from dbus_fast.aio import MessageBus
    except ImportError as err:
        result["error"] = f"dbus_fast unavailable: {err!r}"
        return result

    bus = None
    registered = False
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        async def _iface(path: str, name: str):
            intro = await bus.introspect("org.bluez", path)
            return bus.get_proxy_object("org.bluez", path, intro).get_interface(name)

        managed = await (await _iface("/", "org.freedesktop.DBus.ObjectManager")).call_get_managed_objects()
        device_path = next(
            (
                path
                for path, ifaces in managed.items()
                if "org.bluez.Device1" in ifaces
                and str(ifaces["org.bluez.Device1"]["Address"].value).upper() == address
            ),
            None,
        )
        if device_path is None:
            result["error"] = "BlueZ does not know this device (not scanned by a local adapter)"
            return result
        result["device_path"] = device_path
        props = managed[device_path]["org.bluez.Device1"]
        result["paired_before"] = bool(props["Paired"].value)

        bus.export(AGENT_PATH, _make_agent(result["agent_events"]))
        manager = await _iface("/org/bluez", "org.bluez.AgentManager1")
        await manager.call_register_agent(AGENT_PATH, "KeyboardDisplay")
        registered = True
        try:
            await manager.call_request_default_agent(AGENT_PATH)
        except Exception as err:  # another default agent already exists
            result["default_agent_error"] = repr(err)

        device = await _iface(device_path, "org.bluez.Device1")
        if not await device.get_paired():
            import asyncio

            await asyncio.wait_for(device.call_pair(), timeout)
        await device.set_trusted(True)
        result["ok"] = bool(await device.get_paired())
    except Exception as err:
        result["error"] = f"{type(err).__name__}: {err}"
        _LOGGER.debug("D-Bus pairing with %s failed", address, exc_info=True)
    finally:
        if bus is not None:
            if registered:
                try:
                    manager = await _iface("/org/bluez", "org.bluez.AgentManager1")
                    await manager.call_unregister_agent(AGENT_PATH)
                except Exception:
                    pass
            bus.disconnect()
    return result
