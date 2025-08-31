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

        _LOGGER.debug("Raw HTML snippet (first 500 chars): %s", html[:500])

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

        if not market_obj and not forecast_obj:
            _LOGGER.error("No market or forecast price JSON found")
            raise UpdateFailed("No price data found")

        market_data = []
        if market_obj:
            for t, p in zip(market_obj.get("x", []), market_obj.get("y", [])):
                if p is not None:
                    market_data.append({
                        "time": t,
                        "price": round(float(p) / 1000, 4),
                    })

        forecast_data = []
        if forecast_obj:
            for t, p in zip(forecast_obj.get("x", []), forecast_obj.get("y", [])):
                if p is not None:
                    forecast_data.append({
                        "time": t,
                        "price": round(float(p) / 1000, 4),
                    })

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
        if not self.coordinator.data:
            return None
        merged = self.coordinator.data["merged"]
        last = merged[-1]
        return last.get("market") or last.get("forecast")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return {}
        now = datetime.now(timezone.utc)

        def split_day(data_list, day_offset):
            day = (now + timedelta(days=day_offset)).date()
            values = []
            for x in data_list:
                dt = datetime.fromisoformat(x["time"]).astimezone(timezone.utc)
                if dt.date() == day:
                    val = x.get("market") or x.get("forecast")
                    if val is not None:
                        values.append(round(val, 4))
            return values

        today = split_day(data["merged"], 0)
        tomorrow = split_day(data["merged"], 1)
        dayafter = split_day(data["merged"], 2)

        def build_raw(day_offset):
            day = (now + timedelta(days=day_offset)).date()
            raw = []
            for x in data["merged"]:
                dt = datetime.fromisoformat(x["time"]).astimezone(timezone.utc)
                if dt.date() == day:
                    val = x.get("market") or x.get("forecast")
                    if val is not None:
                        raw.append({
                            "start": dt.isoformat(),
                            "end": (dt + timedelta(hours=1)).isoformat(),
                            "price": round(val, 4),
                        })
            return raw

        raw_today = build_raw(0)
        raw_tomorrow = build_raw(1)
        raw_dayafter = build_raw(2)

        # EVCC format, vanaf vandaag
        evcc = []
        for x in data["merged"]:
            start_dt = datetime.fromisoformat(x["time"]).astimezone(timezone.utc)
            if start_dt.date() < now.date():
                continue
            end_dt = start_dt + timedelta(hours=1)
            value = x.get("market") or x.get("forecast") or 0
            value_int = int(round(value * 1000))  # millikWh / integer
            evcc.append({
                "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "value": value_int
            })

        return {
            "bidding_zone": self.coordinator.bidding_zone,
            "currency": "EUR/kWh",
            "today": today,
            "tomorrow": tomorrow,
            "dayafter": dayafter,
            "raw_today": raw_today,
            "raw_tomorrow": raw_tomorrow,
            "raw_dayafter": raw_dayafter,
            "raw_market": data["market"],
            "raw_forecast": data["forecast"],
            "evcc": evcc,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, str(self.coordinator.bidding_zone))},
            "name": self._name,
            "manufacturer": "Stekker",
        }