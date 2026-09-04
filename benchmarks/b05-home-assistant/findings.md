# Findings — Home Assistant Core (external validation, private)

Written from the project's own issue and pull-request history, before TraceLink was run on the repository. Anchors verified with an independent scan of HEAD.

## HA-001 — PrusaLink reports a version field that may not be a string [HIGH]
### STATUS: OPEN

`ensure_printer_is_supported` reads an undocumented `original` version field returned by the printer. The firmware does not guarantee a string, so the cast that used to be there could fail on real hardware.

## HA-002 — event entity states must be strictly increasing [HIGH]
### STATUS: OPEN

`_trigger_event` has to guarantee a state change when an event fires: two events with the same timestamp would otherwise look like no event at all. This replaced an integration-level workaround.

## HA-003 — orjson does not serialise subclasses of str [MEDIUM]
### STATUS: OPEN

`SerializationError` covers a workaround for an upstream orjson limitation: dict keys that are subclasses of `str` fail to serialise. The workaround is to be reverted once orjson fixes it — it is not our design.

## HA-004 — SmartThings AC setpoint bounds are Celsius, and the base class converts [HIGH]
### STATUS: OPEN

`_get_setpoint_range_value` must not return the default bounds verbatim: those constants are Celsius and `ClimateEntity` converts them again, which was the regression.

## HA-005 — HomeKit Controller shows stale cached values at startup [MEDIUM]
### STATUS: OPEN

`remove_pollable_characteristics` participates in the startup path where entities briefly show values restored from storage before the first poll. Some accessories then report figures that were never current.

## HA-006 — a WiZ bulb can end up reporting no colour mode at all [HIGH]
### STATUS: OPEN

In `homeassistant/components/wiz/light.py`, `_async_update_attrs` has to cope with a bulb that sends no colour values while it is on. The light platform refuses an entity that reports no colour mode, so the integration must supply one.

## HA-007 — some Matter fans report 255 as a sentinel, not a percentage [MEDIUM]
### STATUS: OPEN

In `homeassistant/components/matter/fan.py`, `_update_from_device` sees `PercentCurrent` of 255 from some devices while `FanMode` is Auto. It is a sentinel meaning 'no real value', not a speed.

## HA-008 — some Fibaro covers carry a state attribute that says nothing [MEDIUM]
### STATUS: OPEN

In `homeassistant/components/fibaro/cover.py`, `set_cover_tilt_position` belongs to devices whose state attribute exists but reads `unknown`. The entity must report None so open and close stay available.

## HA-009 — older Vizio firmware has no /state_extended endpoint [HIGH]
### STATUS: OPEN

`homeassistant/components/vizio/manifest.json` pins the library version that restored the fallback: the coordinator polls `/state_extended` and older firmware answers 404, which is not the 'unsupported' error the fallback was keyed on.

## HA-010 — the Airthings Corentium Home 2 stops advertising if polled too often [MEDIUM]
### STATUS: OPEN

`homeassistant/components/airthings_ble/config_flow.py` sets the scan interval for this model. The device has a firmware issue: too many BLE connections over time and it goes silent.

## HA-011 — some BSB-Lan installations answer `---` instead of a temperature [MEDIUM]
### STATUS: OPEN

`homeassistant/components/bsblan/climate.py` must check the current temperature before casting: an installation with no room sensor returns the string `---`.

## HA-012 — some Fibaro climate devices report no unit [MEDIUM]
### STATUS: OPEN

`homeassistant/components/fibaro/climate.py` cannot assume a unit is present. In those cases a second device usually forms the climate entity and carries it.

## HA-013 — one Tradfri remote without a firmware version removed every battery sensor [HIGH]
### STATUS: OPEN

`homeassistant/components/tradfri/entity.py` builds every entity in a single setup pass, so a `KeyError` from one device with no firmware field took the whole sensor platform down with it.

## HA-014 — Airzone reports its own temperature step [MEDIUM]
### STATUS: OPEN

`homeassistant/components/airzone/climate.py` used to hardcode the target temperature step from a local constant while the library already parses the device's own `temp_step`.

## HA-015 — ZHA device triggers need the quirk resolver forwarded [MEDIUM]
### STATUS: OPEN

Device triggers are unavailable when ZHA has not yet reached the coordinator while automations are being set up: `async_setup_entry` must forward the device resolver so quirks resolve.

## HA-016 — the iBeacon integration ignores beacons with a default name [MEDIUM]
### STATUS: OPEN

`async_step_user` gained an allowlist because ignoring unnamed beacons — which exists to avoid flooding users with junk devices — also hides legitimate ones.

## HA-017 — the template helper is no longer a single module [MEDIUM]
### STATUS: OPEN

The `to_json` workaround for orjson lived in `homeassistant/helpers/template.py`. That path no longer exists — the helper became a package — so this note points at nothing today.

## HA-018 — MELCloud invalidates the token when it pushes a firmware update [MEDIUM]
### STATUS: OPEN

The setup path used to be `mel_devices_setup`; that name is gone. The knowledge survives it: a firmware push expires the token and the integration has to re-authenticate rather than fail.

## HA-019 — some UniFi controllers never report proper client data [MEDIUM]
### STATUS: OPEN

For those controllers the tracker has to fall back to events. The callback that carried this was reworked; nothing in the current code answers to the old name.

## HA-020 — release cadence is not visible from the code [MEDIUM]
### STATUS: OPEN

Monthly releases and the beta window are a project process, not a property of any module: no function represents them.
