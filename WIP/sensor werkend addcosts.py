import logging
from datetime import datetime, timedelta, timezone
import aiohttp
import re
import json

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, CoordinatorEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.template import Template
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import (
    SensorDeviceClass,
    SensorStateClass,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class StekkerAPI:
    """Fetch market & forecast prices from Stekker."""

    BASE_URL = "https://stekker.app/epex-forecast"

    def __init__(self, zone: str):
        self.zone = zone

    async def fetch_prices(self):
        url = f"{self.BASE_URL}?advanced_view=&region={self.zone}&unit=MWh"
        _LOGGER.debug("Fetching prices from URL: %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                html = await resp.text()

        match = re.search(r'data-epex-forecast-graph-data-value="(.+?)"', html, re.DOTALL)
        if not match:
            raise UpdateFailed("No price data found in HTML")

        raw_data = match.group(1).replace("&quot;", '"')
        try:
            data_array = json.loads(raw_data)
        except Exception as e:
            raise UpdateFailed(f"Failed parsing JSON: {e}")

        market_obj = next((o for o in data_array if "Market price" in o.get("name", "")), None)
        forecast_obj = next((o for o in data_array if "Forecast price" in o.get("name", "")), None)

        market_data = [
            {"time": t, "price": float(p)/1000}  # EUR/MWh → EUR/kWh
            for t, p in zip(market_obj.get("x", []), market_obj.get("y", [])) if p is not None
        ] if market_obj else []

        forecast_data = [
            {"time": t, "price": float(p)/1000}
            for t, p in zip(forecast_obj.get("x", []), forecast_obj.get("y", [])) if p is not None
        ] if forecast_obj else []

        merged_data = market_data + forecast_data
        merged_data.sort(key=lambda x: x["time"])

        return {
            "market": market_data,
            "forecast": forecast_data,
            "merged": merged_data
        }


class StekkerCoordinator(DataUpdateCoordinator):
    """Coordinator for Stekker data."""

    def __init__(self, hass: HomeAssistant, zone: str, additional_costs: str | None):
        self.zone = zone
        self.api = StekkerAPI(zone)
        self.additional_costs = Template(additional_costs, hass) if additional_costs else None

        super().__init__(
            hass,
            _LOGGER,
            name=f"Stekker {self.zone}",
            update_interval=timedelta(minutes=15),
            update_method=self._async_update_data,
        )

        # Exact kwartier trigger
        async_track_time_change(
            hass,
            self._quarter_hour_callback,
            minute=(0, 15, 30, 45),
            second=0,
        )

    async def _async_update_data(self):
        """Fetch prices from API"""
        try:
            data = await self.api.fetch_prices()
            return data
        except Exception as e:
            raise UpdateFailed(e)

    @callback
    def _quarter_hour_callback(self, now):
        """Force a refresh on the quarter hour"""
        # Zorg dat dit altijd in de event loop gebeurt
        self.hass.async_create_task(self.async_request_refresh())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    zone = entry.data["bidding_zone"]
    additional_costs = entry.options.get("additional_costs")
    coordinator = StekkerCoordinator(hass, zone, additional_costs)

    # Start coordinator en wacht eerste fetch
    await coordinator.async_config_entry_first_refresh()

    async_add_entities([StekkerSensor(coordinator)], True)
    return True


class StekkerSensor(CoordinatorEntity, SensorEntity):
    """Stekker electricity price sensor."""

    # Zorg dat HA weet dat dit een geld-sensor is
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_suggested_display_precision = 4
    _attr_force_update = True

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self._name = f"Stekker {self.coordinator.zone}"
        self._attr_unique_id = f"stekker_{self.coordinator.zone}"

    @property
    def name(self):
        return self._name

    @property
    def native_value(self):
        """Huidige uurprijs (EUR/kWh)."""
        if not self.coordinator.data:
            return None

        now = datetime.now().astimezone()
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        for entry in self.coordinator.data["merged"]:
            entry_dt = datetime.fromisoformat(entry["time"]).astimezone()
            if entry_dt <= current_hour < entry_dt + timedelta(hours=1):
                base_price = round(entry["price"], 4)
                if self.coordinator.additional_costs:
                    try:
                        extra = float(
                            self.coordinator.additional_costs.async_render(
                                now=now, current_price=base_price
                            )
                        )
                        return round(base_price + extra, 4)
                    except Exception as e:
                        _LOGGER.error("Error in additional_costs template: %s", e)
                        return base_price
                return base_price
        return None

    @property
    def extra_state_attributes(self):
        """Extra data voor statistieken en integraties (zoals EVCC)."""
        data = self.coordinator.data
        if not data:
            return {}

        now_local = datetime.now().astimezone()
        start_of_today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        def split_day(data_list, day_offset):
            day = (start_of_today_local + timedelta(days=day_offset)).date()
            return [
                round(x["price"], 4)
                for x in data_list
                if datetime.fromisoformat(x["time"]).astimezone().date() == day
            ]

        today = split_day(data["merged"], 0)
        tomorrow = split_day(data["merged"], 1)
        dayafter = split_day(data["merged"], 2)

        # EVCC-compatible lijst
        evcc_list = []
        for x in data["merged"]:
            local_dt = datetime.fromisoformat(x["time"]).astimezone()
            if local_dt >= start_of_today_local:
                start_utc = (local_dt - local_dt.utcoffset()).replace(tzinfo=timezone.utc)
                end_utc = start_utc + timedelta(hours=1)
                evcc_list.append({
                    "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "value": round(x["price"], 3)
                })

        return {
            "bidding_zone": self.coordinator.zone,
            "currency": "EUR/kWh",
            "today": today,
            "tomorrow": tomorrow,
            "dayafter": dayafter,
            "raw_market": data["market"],
            "raw_forecast": data["forecast"],
            "evcc": json.dumps(evcc_list),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, str(self.coordinator.zone))},
            "name": self._name,
            "manufacturer": "Stekker",
        }