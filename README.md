# Reefability for Home Assistant

See your reef tanks in Home Assistant: the latest readings, tasks, out-of-range readings and alerts from [Reefability](https://reefability.com), feed mode buttons, and services to log a test result, tick a task off or ask Reefer AI. You can also send Home Assistant sensors (probes, room climate, leak sensors) to Reefability.

## Install (HACS)

1. In Home Assistant open **HACS > Integrations**, then the menu (three dots) > **Custom repositories**.
2. Add this repository's URL with the category **Integration**.
3. Find **Reefability** in HACS and download it.
4. Restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration** and pick **Reefability**.

Manual install: copy `custom_components/reefability` into your Home Assistant `config/custom_components/` folder and restart.

## Connect your account

1. In the Reefability app open **More > Home Assistant > Create token**.
2. Copy the token (48 characters) and paste it into Home Assistant.

The token only works for Home Assistant and you can remove it in the app at any time. If you remove it, Home Assistant asks you for a new one under Settings > Devices & services.

## What appears

Each tank becomes a device (manufacturer Reefability, model Reef tank). Data refreshes every 5 minutes.

**Sensors**, one per reading the tank has (Alkalinity, Calcium, Magnesium, Nitrate, Phosphate, Temperature, Salinity, pH, Potassium, Ammonia, Nitrite and any other test you log):

- The value in your unit (°F or °C, ppm, dKH, SG, mV ...), shown with the same digits as the app. Values are never rounded up.
- Attributes: `status` (ok, high or low), `ideal_low` and `ideal_high` (your ideal range), `ideal_range_source`, `measured_at`, `source` (the device or test kit).
- A reading Reefability flags as a probe problem (Check probe in the app, for example a salinity probe reading 1.000 SG) is never shown as the value. The sensor keeps the newest trustworthy reading and sets `check_probe` to true, with `suspect_value` and `suspect_at`. When every recent reading of a parameter is suspect, the sensor turns unavailable.
- A reading that stops being reported turns unavailable instead of disappearing.

Plus, per tank: **Tasks due today**, **Tasks overdue** (with a `tasks` attribute listing id, name and due date of the first few) and **Readings out of range** (with the reading names).

**Binary sensors**, per tank:

- **Out of range**: on when any reading is outside its ideal range.
- **Leak**: on when Reefability has a leak alert for the tank.
- **Device offline**: on when a connected controller or device is offline. Attributes list the alerts.

**Buttons**, per tank: **Start feed mode** and **Stop feed mode**. These need a Reefability Display connected to the tank; without one, Home Assistant shows a message saying so.

## Services

`reefability.log_reading`: save a test result.

```yaml
action: reefability.log_reading
data:
  device_id: <your tank device>   # or tank_id: 1
  parameter: Alkalinity           # Alkalinity, Calcium, Magnesium, Nitrate, Phosphate, Temperature, Salinity, pH, ORP, Potassium, Ammonia, Nitrite
  value: 8.2
  unit: dKH                       # optional; without it the tank's usual unit is used
```

`reefability.complete_task`: tick a task off. Task ids are on the Tasks sensors' `tasks` attribute.

```yaml
action: reefability.complete_task
data:
  task_id: 1002
```

`reefability.ask`: ask Reefer AI a short question. It returns `answer`.

```yaml
action: reefability.ask
data:
  device_id: <your tank device>
  question: Is my alkalinity stable this week?
response_variable: reefer
```

## Send to Reefability

Open **Settings > Devices & services > Reefability > Configure**.

1. Pick the tank (only asked when you have more than one).
2. Pick the Home Assistant entities to send to it:
   - **Tank probes**: one sensor each for water temperature, salinity, pH, ORP, alkalinity, calcium, magnesium, nitrate, phosphate, potassium. Units are converted for you (for example °C to °F, ppt to SG, meq/L to dKH).
   - **Room**: room temperature, room humidity, CO2.
   - **Leak sensors**: moisture binary sensors. When one turns wet, Reefability sends you an urgent push.
   - **Power** and **Other**: kept by Reefability for later use.

Run Configure again to link sensors to another tank; each tank keeps its own list.

Changes are sent within 30 seconds (a leak sensor straight away), and every linked entity is sent again every 15 minutes so readings keep arriving when nothing changes. Reefability keeps at most one reading per sensor every 10 minutes. Unavailable or unknown states are skipped.

## Privacy

The integration talks only to the Reefability API (reefability.com) over HTTPS with your token. It sends nothing until you link entities under Configure, and then only those entities.

## Licence

MIT, see [LICENSE](LICENSE). Full install guide: [reefability.com/home-assistant](https://reefability.com/home-assistant).
