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
        url = f"{self.BASE_URL}?region={self.bidding_zone}&unit=MWh"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                html = await resp.text()

        match = re.search(r'data-epex-forecast-graph-data-value="(.+?)"', html, re.DOTALL)
        if not match:
            raise UpdateFailed("No price data found")

        raw_data = match.group(1).replace("&quot;", '"')
        try:
            data_array = json.loads(raw_data)
        except Exception as e:
            raise UpdateFailed(f"Failed parsing JSON array: {e}")

        # Market & forecast
        market_obj = next((obj for obj in data_array if "Market price" in obj.get("name", "")), None)
        forecast_obj = next((obj for obj in data_array if "Forecast price" in obj.get("name", "")), None)

        market_data = [{"time": t, "price": float(p)/1000} for t, p in zip(market_obj["x"], market_obj["y"])] if market_obj else []
        forecast_data = [{"time": t, "price": float(p)/1000} for t, p in zip(forecast_obj["x"], forecast_obj["y"])] if forecast_obj else []

        # Merge by time (market if exists else forecast)
        all_times = sorted(set([x["time"] for x in market_data + forecast_data]))
        merged_data = []
        for t in all_times:
            m = next((x["price"] for x in market_data if x["time"] == t), None)
            f = next((x["price"] for x in forecast_data if x["time"] == t), None)
            merged_data.append({"time": t, "market": m, "forecast": f})

        return {
            "market": market_data,
            "forecast": forecast_data,
            "merged": merged_data,
        }


class StekkerCoordinator(DataUpdateCoordinator):
    """Coordinator for fetching Stekker data."""

    def __init__(self, hass: HomeAssistant, entry_data: dict):
        self.bidding_zone = entry_data.get("bidding_zone", DEFAULT_BIDDING_ZONE)
        if self.bidding_zone not in BIDDING_ZONES_MAP:
            _LOGGER.warning("Bidding zone %s not supported!", self.bidding_zone)

        self.api = StekkerAPI(self.bidding_zone)
        super().__init__(
            hass,
            _LOGGER,
            name=f"Stekker {self.bidding_zone}",
            update_interval=UPDATE_INTERVAL,
            update_method=self._async_update_data,
        )

    async def _async_update_data(self):
        try:
            return await self.api.fetch_prices()
        except Exception as err:
            _LOGGER.error("Error fetching Stekker data: %s", err)
            raise UpdateFailed(err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data.setdefault(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        coordinator = StekkerCoordinator(hass, entry.data)
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][entry.entry_id] = coordinator
    async_add_entities([StekkerSensor(coordinator)], True)
    return True


class StekkerSensor(Entity):
    """Representation of Stekker market price sensor."""

    def __init__(self, coordinator: StekkerCoordinator):
        self.coordinator = coordinator
        self._name = f"Stekker {self.coordinator.bidding_zone}"
        self._attr_unique_id = f"stekker_{self.coordinator.bidding_zone}"

    @property
    def name(self):
        return self._name

    @property
    def should_poll(self) -> bool:
        return False

    async def async_update(self):
        await self.coordinator.async_request_refresh()

    @property
    def state(self):
        """Current price for current hour, rounded to 4 decimals."""
        if not self.coordinator.data:
            return None
        now_local = datetime.now().astimezone()
        current_hour = now_local.replace(minute=0, second=0, microsecond=0)
        current_obj = next(
            (x for x in self.coordinator.data["merged"]
             if datetime.fromisoformat(x["time"]).astimezone().replace(minute=0, second=0, microsecond=0) == current_hour),
            None
        )
        if current_obj:
            return round(current_obj.get("market") or current_obj.get("forecast") or 0, 4)
        return None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}

        now_local = datetime.now().astimezone()
        start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

        def split_day(day_offset):
            day = (start_today + timedelta(days=day_offset)).date()
            return [
                round(x.get("market") or x.get("forecast") or 0, 4)
                for x in data["merged"]
                if datetime.fromisoformat(x["time"]).astimezone().date() == day
            ]

        today = split_day(0)
        tomorrow = split_day(1)

        # Raw arrays (market & forecast)
        raw_today = [
            {"start": x["time"],
             "end": (datetime.fromisoformat(x["time"]) + timedelta(hours=1)).isoformat(),
             "price": round(x.get("market") or x.get("forecast") or 0, 4)}
            for x in data["merged"]
            if datetime.fromisoformat(x["time"]).astimezone().date() == now_local.date()
        ]

        raw_tomorrow = [
            {"start": x["time"],
             "end": (datetime.fromisoformat(x["time"]) + timedelta(hours=1)).isoformat(),
             "price": round(x.get("market") or x.get("forecast") or 0, 4)}
            for x in data["merged"]
            if datetime.fromisoformat(x["time"]).astimezone().date() == (now_local + timedelta(days=1)).date()
        ]

        # EVCC format (vanaf vandaag)
        evcc_list = [
            {"start": x["time"],
             "end": (datetime.fromisoformat(x["time"]) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "value": round(x.get("market") or x.get("forecast") or 0, 3)}
            for x in data["merged"]
            if datetime.fromisoformat(x["time"]).astimezone() >= start_today
        ]

        return {
            "bidding_zone": self.coordinator.bidding_zone,
            "currency": "EUR/kWh",
            "today": today,
            "tomorrow": tomorrow,
            "raw_today": raw_today,
            "raw_tomorrow": raw_tomorrow,
            "raw_market": [{"time": x["time"], "price": round(x["price"], 4)} for x in data["market"]],
            "raw_forecast": [{"time": x["time"], "price": round(x["price"], 4)} for x in data["forecast"]],
            "evcc": json.dumps(evcc_list),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, str(self.coordinator.bidding_zone))},
            "name": self._name,
            "manufacturer": "Stekker",
        }