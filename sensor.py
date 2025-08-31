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
    """Handles scraping Stekker webpage for market and forecast prices."""

    BASE_URL = "https://stekker.app/epex-forecast"

    def __init__(self, bidding_zone: str):
        self.bidding_zone = bidding_zone

    async def fetch_prices(self):
        _LOGGER.debug("Fetching prices for bidding zone: %s", self.bidding_zone)
        url = f"{self.BASE_URL}?region={self.bidding_zone}&unit=MWh"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                html = await resp.text()

        _LOGGER.debug("Raw HTML snippet (first 900 chars): %s", html[:900])

        # Extract data-epex-forecast-graph-data-value
        match = re.search(r'data-epex-forecast-graph-data-value="(.+?)"', html, re.DOTALL)
        if not match:
            _LOGGER.error("No forecast data attribute found")
            raise UpdateFailed("No price data found")

        raw_data = match.group(1).replace("&quot;", '"')
        try:
            data_array = json.loads(raw_data)
        except Exception as e:
            _LOGGER.error("Failed parsing JSON array: %s", e)
            raise UpdateFailed(e)

        market_obj = next((obj for obj in data_array if "Market price" in obj.get("name", "")), None)
        forecast_obj = next((obj for obj in data_array if "Forecast price" in obj.get("name", "")), None)

        market_data = []
        if market_obj:
            for t, p in zip(market_obj.get("x", []), market_obj.get("y", [])):
                if p is not None:
                    market_data.append({"time": t, "price": float(p) / 1000})

        forecast_data = []
        if forecast_obj:
            for t, p in zip(forecast_obj.get("x", []), forecast_obj.get("y", [])):
                if p is not None:
                    forecast_data.append({"time": t, "price": float(p) / 1000})

        all_times = sorted(set([x["time"] for x in market_data + forecast_data]))

       # merged_data = []
       # for t in all_times:
       #     m = next((x["price"] for x in market_data if x["time"] == t), None)
       #     f = next((x["price"] for x in forecast_data if x["time"] == t), None)
       #     merged_data.append({"time": t, "market": m, "forecast": f})

                # Merge market + forecast without overlap
        merged_data = []
        last_market_time = market_data[-1]["time"] if market_data else None

        # Market entries
        for item in market_data:
            merged_data.append({"time": item["time"], "market": item["price"], "forecast": None})

        # Forecast entries (start na laatste market)
        for item in forecast_data:
            if last_market_time is None or item["time"] > last_market_time:
                merged_data.append({"time": item["time"], "market": None, "forecast": item["price"]})

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

    sensors = []
    zones = BIDDING_ZONES_MAP.get(coordinator.bidding_zone, [coordinator.bidding_zone])
    for zone_code in zones:
        sensors.append(StekkerSensor(coordinator, zone_code))

    async_add_entities(sensors, True)
    return True


class StekkerSensor(Entity):
    """Representation of Stekker market price sensor."""

    def __init__(self, coordinator: StekkerCoordinator, zone_code: str):
        self.coordinator = coordinator
        self.zone_code = zone_code
        self._name = f"Stekker {self.coordinator.bidding_zone} ({zone_code})"
        self._attr_unique_id = f"stekker_{self.coordinator.bidding_zone}_{zone_code}"

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
        """Current value rounded to 4 decimals."""
        if not self.coordinator.data:
            return None
        merged = self.coordinator.data["merged"]
        last = merged[-1]
        value = last.get("market") or last.get("forecast")
        return round(value, 4) if value is not None else None

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
                round(x.get("market") or x.get("forecast") or 0, 4)
                for x in data_list
                if datetime.fromisoformat(x["time"]).astimezone().date() == day
            ]

        today = split_day(data["merged"], 0)
        tomorrow = split_day(data["merged"], 1)
        dayafter = split_day(data["merged"], 2)

        raw_today = [
            {
                "start": x["time"],
                "end": (datetime.fromisoformat(x["time"]) + timedelta(hours=1)).isoformat(),
                "price": round(x.get("market") or x.get("forecast") or 0, 4),
            }
            for x in data["merged"]
            if datetime.fromisoformat(x["time"]).astimezone().date() == now_local.date()
        ]

        raw_tomorrow = [
            {
                "start": x["time"],
                "end": (datetime.fromisoformat(x["time"]) + timedelta(hours=1)).isoformat(),
                "price": round(x.get("market") or x.get("forecast") or 0, 4),
            }
            for x in data["merged"]
            if datetime.fromisoformat(x["time"]).astimezone().date() == (now_local + timedelta(days=1)).date()
        ]

        raw_dayafter = [
            {
                "start": x["time"],
                "end": (datetime.fromisoformat(x["time"]) + timedelta(hours=1)).isoformat(),
                "price": round(x.get("market") or x.get("forecast") or 0, 4),
            }
            for x in data["merged"]
            if datetime.fromisoformat(x["time"]).astimezone().date() == (now_local + timedelta(days=2)).date()
        ]

        evcc_list = []
        for x in data["merged"]:
            start_dt = datetime.fromisoformat(x["time"]).astimezone()
            if start_dt >= start_of_today_local:
                end_dt = start_dt + timedelta(hours=1)
                value = x.get("market") or x.get("forecast") or 0
                evcc_list.append({
                    "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "value": round(value, 3)
                })

        evcc_json = json.dumps(evcc_list)

        return {
            "bidding_zone": self.coordinator.bidding_zone,
            "zone_code": self.zone_code,
            "currency": "EUR/kWh",
            "today": today,
            "tomorrow": tomorrow,
            "dayafter": dayafter,
            "raw_today": raw_today,
            "raw_tomorrow": raw_tomorrow,
            "raw_dayafter": raw_dayafter,
            #"raw_market": [{"time": x["time"], "price": round(x["price"], 4)} for x in data["market"]],
            #"raw_forecast": [{"time": x["time"], "price": round(x["price"], 4)} for x in data["forecast"]],
            "evcc": evcc_json,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, f"{self.coordinator.bidding_zone}_{self.zone_code}")},
            "name": self._name,
            "manufacturer": "Stekker",
        }