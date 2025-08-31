import logging
from datetime import datetime, timedelta, timezone
import aiohttp
import re
import json

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity import Entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, BIDDING_ZONES_MAP, DEFAULT_BIDDING_ZONE

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(minutes=15)


class StekkerAPI:
    """Handles fetching Stekker webpage for market and forecast prices."""

    BASE_URL = "https://stekker.app/epex-forecast"

    def __init__(self, bidding_zone: str):
        self.bidding_zone = bidding_zone

    async def fetch_prices(self):
        url = f"{self.BASE_URL}?advanced_view=&region={self.bidding_zone}&unit=MWh"
        _LOGGER.debug("Fetching prices from URL: %s", url)

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                html = await resp.text()

        # Extract JSON data from the HTML attribute
        match = re.search(r'data-epex-forecast-graph-data-value="(.+?)"', html, re.DOTALL)
        if not match:
            _LOGGER.error("No forecast data found in HTML")
            raise UpdateFailed("No price data found")

        raw_data = match.group(1).replace("&quot;", '"')
        try:
            data_array = json.loads(raw_data)
        except Exception as e:
            _LOGGER.error("Failed parsing JSON array: %s", e)
            raise UpdateFailed(e)

        # Separate market and forecast
        market_obj = next((obj for obj in data_array if "Market price" in obj.get("name", "")), None)
        forecast_obj = next((obj for obj in data_array if "Forecast price" in obj.get("name", "")), None)

        if not market_obj and not forecast_obj:
            _LOGGER.error("No market or forecast data in JSON")
            raise UpdateFailed("No price data found")

        market_data = [
            {"time": t, "price": float(p)/1000}  # EUR/MWh -> EUR/kWh
            for t, p in zip(market_obj.get("x", []), market_obj.get("y", []))
            if p is not None
        ] if market_obj else []

        forecast_data = [
            {"time": t, "price": float(p)/1000}
            for t, p in zip(forecast_obj.get("x", []), forecast_obj.get("y", []))
            if p is not None
        ] if forecast_obj else []

        # Merge for EVCC
        merged_data = market_data + forecast_data
        merged_data.sort(key=lambda x: x["time"])

        _LOGGER.debug("Fetched %d market entries, %d forecast entries", len(market_data), len(forecast_data))
        return {
            "market": market_data,
            "forecast": forecast_data,
            "merged": merged_data
        }


class StekkerCoordinator(DataUpdateCoordinator):
    """Coordinator for Stekker data."""

    def __init__(self, hass: HomeAssistant, bidding_zone: str):
        self.bidding_zone = bidding_zone
        self.api = StekkerAPI(bidding_zone)

        super().__init__(
            hass,
            _LOGGER,
            name=f"Stekker {self.bidding_zone}",
            update_interval=UPDATE_INTERVAL,
            update_method=self._async_update_data,
        )

    async def _async_update_data(self):
        try:
            data = await self.api.fetch_prices()
            _LOGGER.debug("Data updated for zone %s", self.bidding_zone)
            return data
        except Exception as e:
            _LOGGER.error("Error fetching Stekker data for %s: %s", self.bidding_zone, e)
            raise UpdateFailed(e)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinators = []

    zones = entry.data.get("bidding_zones", [DEFAULT_BIDDING_ZONE])
    for zone in zones:
        coordinator = StekkerCoordinator(hass, zone)
        await coordinator.async_config_entry_first_refresh()
        coordinators.append(coordinator)

    entities = [StekkerSensor(coordinator) for coordinator in coordinators]
    async_add_entities(entities, True)
    return True


class StekkerSensor(Entity):
    """Stekker price sensor."""

    def __init__(self, coordinator: StekkerCoordinator):
        self.coordinator = coordinator
        self._name = f"Stekker {self.coordinator.bidding_zone}"
        self._attr_unique_id = f"stekker_{self.coordinator.bidding_zone}"

    @property
    def name(self):
        return self._name

    @property
    def should_poll(self):
        return False  # polling via coordinator

    async def async_update(self):
        await self.coordinator.async_request_refresh()

    @property
    def state(self):
        """Current price for the current hour (local time)."""
        if not self.coordinator.data:
            return None

        now_local = datetime.now().astimezone()
        current_hour = now_local.replace(minute=0, second=0, microsecond=0)

        for entry in self.coordinator.data["merged"]:
            entry_dt = datetime.fromisoformat(entry["time"]).astimezone()
            if entry_dt <= current_hour < entry_dt + timedelta(hours=1):
                return round(entry["price"], 4)
        return None

    @property
    def extra_state_attributes(self):
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

        # EVCC: start vanaf 00:00 lokale tijd, UTC timestamps
        evcc_list = []
        for x in data["merged"]:
            start_dt_local = datetime.fromisoformat(x["time"]).astimezone()
            if start_dt_local >= start_of_today_local:
                start_utc = start_dt_local.astimezone(timezone.utc)
                evcc_list.append({
                    "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": (start_utc + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "value": round(x["price"], 3)
                })

        evcc_json = json.dumps(evcc_list)

        return {
            "bidding_zone": self.coordinator.bidding_zone,
            "currency": "EUR/kWh",
            "today": today,
            "tomorrow": tomorrow,
            "dayafter": dayafter,
            "raw_market": data["market"],
            "raw_forecast": data["forecast"],
            "evcc": evcc_json,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, str(self.coordinator.bidding_zone))},
            "name": self._name,
            "manufacturer": "Stekker",
        }