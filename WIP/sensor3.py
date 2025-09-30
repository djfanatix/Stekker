import logging
from datetime import datetime, timedelta, timezone
import aiohttp
import re
import json

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, CoordinatorEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.entity import Entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(minutes=15)


class StekkerAPI:
    """Fetch market and forecast prices from Stekker."""

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

        # Extract JSON from HTML
        match = re.search(r'data-epex-forecast-graph-data-value="(.+?)"', html, re.DOTALL)
        if not match:
            raise UpdateFailed("No price data found in HTML")

        raw_data = match.group(1).replace("&quot;", '"')
        try:
            data_array = json.loads(raw_data)
        except Exception as e:
            raise UpdateFailed(f"JSON parse error: {e}")

        market_obj = next((o for o in data_array if "Market price" in o.get("name", "")), None)
        forecast_obj = next((o for o in data_array if "Forecast price" in o.get("name", "")), None)

        market_data = [{"time": t, "price": float(p)/1000} for t, p in zip(market_obj.get("x", []), market_obj.get("y", [])) if p is not None] if market_obj else []
        forecast_data = [{"time": t, "price": float(p)/1000} for t, p in zip(forecast_obj.get("x", []), forecast_obj.get("y", [])) if p is not None] if forecast_obj else []

        merged = sorted(market_data + forecast_data, key=lambda x: x["time"])
        _LOGGER.debug("Fetched %d market, %d forecast entries", len(market_data), len(forecast_data))
        return {"market": market_data, "forecast": forecast_data, "merged": merged}


class StekkerCoordinator(DataUpdateCoordinator):
    """Coordinator for Stekker data."""

    def __init__(self, hass: HomeAssistant, zone: str, additional_costs: str | None):
        self.zone = zone
        self.api = StekkerAPI(zone)
        self.additional_costs = Template(additional_costs, hass) if additional_costs else None

        super().__init__(
            hass,
            _LOGGER,
            name=f"Stekker {zone}",
            update_interval=timedelta(minutes=15),
            update_method=self._async_update_data,
        )

        # Trigger exact op het uur
        async_track_time_change(hass, lambda *_: self.async_request_refresh(), minute=0, second=5)

    async def _async_update_data(self):
        try:
            return await self.api.fetch_prices()
        except Exception as e:
            _LOGGER.error("Error fetching data for %s: %s", self.zone, e)
            raise UpdateFailed(e)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    zone = entry.data["bidding_zone"]
    currency = entry.options.get("currency", "EUR")
    additional_costs = entry.options.get("additional_costs")
    coordinator = StekkerCoordinator(hass, zone, additional_costs)
    await coordinator.async_config_entry_first_refresh()
    async_add_entities([StekkerSensor(coordinator, currency)])
    return True


class StekkerSensor(CoordinatorEntity, Entity):
    """Stekker price sensor."""

    def __init__(self, coordinator: StekkerCoordinator, currency: str):
        super().__init__(coordinator)
        self._name = f"Stekker {coordinator.zone}"
        self._attr_unique_id = f"stekker_{coordinator.zone}"
        self._currency = currency

    @property
    def name(self):
        return self._name

    @property
    def native_unit_of_measurement(self):
        return f"{self._currency}/kWh"

    @property
    def state(self):
        """Current price for the current hour (local time) + optional additional costs."""
        data = self.coordinator.data
        if not data:
            return None

        now = datetime.now().astimezone()
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        for entry in data["merged"]:
            entry_dt = datetime.fromisoformat(entry["time"]).astimezone()
            if entry_dt <= current_hour < entry_dt + timedelta(hours=1):
                price = round(entry["price"], 4)
                if self.coordinator.additional_costs:
                    try:
                        extra = float(self.coordinator.additional_costs.async_render(now=now, current_price=price))
                        price += extra
                    except Exception as e:
                        _LOGGER.error("Error rendering additional_costs: %s", e)
                return round(price, 4)
        return None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}

        now_local = datetime.now().astimezone()
        start_of_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        def split_day(lst, offset):
            day = (start_of_today + timedelta(days=offset)).date()
            return [round(x["price"], 4) for x in lst if datetime.fromisoformat(x["time"]).astimezone().date() == day]

        today = split_day(data["merged"], 0)
        tomorrow = split_day(data["merged"], 1)
        dayafter = split_day(data["merged"], 2)

        # EVCC list: start 00:00 local -> UTC
        evcc_list = []
        for x in data["merged"]:
            local_dt = datetime.fromisoformat(x["time"]).astimezone()
            if local_dt >= start_of_today:
                start_utc = (local_dt - local_dt.utcoffset()).replace(tzinfo=timezone.utc)
                evcc_list.append({
                    "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": (start_utc + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "value": round(x["price"], 3)
                })
                

        return {
            "bidding_zone": self.coordinator.zone,
            "currency": f"{self._currency}/kWh",
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
            "identifiers": {(DOMAIN, self.coordinator.zone)},
            "name": self._name,
            "manufacturer": "Stekker",
        }