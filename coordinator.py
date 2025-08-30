from datetime import datetime, timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import aiohttp
import re
import json
import logging
from .const import BIDDING_ZONES

_LOGGER = logging.getLogger(__name__)
DEFAULT_HOST = "stekker.app"
DEFAULT_TIMEOUT = 30
UPDATE_INTERVAL = timedelta(minutes=30)

REGEX_MARKET = re.compile(r'{&quot;x&quot;:(.*?)&quot;Market price&quot;')
REGEX_FORECAST = re.compile(r'{&quot;x&quot;:(.*?)&quot;Forecast price&quot;')


class StekkerCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, bidding_zone: str, markup: float = 1.0):
        super().__init__(
            hass,
            _LOGGER,
            name=f"Stekker {bidding_zone}",
            update_interval=UPDATE_INTERVAL,
        )
        self.host = DEFAULT_HOST
        self.bidding_zone = bidding_zone
        self.markup = markup
        self.last_response = None

    async def _async_update_data(self):
        try:
            today = datetime.utcnow()
            tomorrow = today + timedelta(days=1)
            start_iso = today.replace(minute=0, second=0, microsecond=0).isoformat()
            end_iso = tomorrow.replace(minute=0, second=0, microsecond=0).isoformat()

            region = BIDDING_ZONES[self.bidding_zone]
            url = f"https://{self.host}/epex-forecast?advanced_view=&region={region}&unit=MWh&filter_from={start_iso}&filter_to={end_iso}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=DEFAULT_TIMEOUT) as resp:
                    if resp.status != 200:
                        raise UpdateFailed(f"HTTP Error: {resp.status}")
                    html = await resp.text()
                    self.last_response = html

            return self._parse_prices(html)

        except Exception as e:
            raise UpdateFailed(f"Error fetching Stekker data: {e}")

    def _parse_prices(self, html):
        match = REGEX_MARKET.search(html)
        if not match:
            raise UpdateFailed("No market price data found")

        data_str = match.group(1)
        try:
            parts = data_str.split(',"y":')
            times = json.loads(parts[0])
            prices = json.loads(parts[1].split(',"line"')[0])
        except Exception as e:
            raise UpdateFailed(f"Error parsing price data: {e}")

        if len(times) != len(prices):
            raise UpdateFailed("Times and prices length mismatch")

        hourly = [
            {"time": datetime.fromisoformat(t), "price": round(p * self.markup, 2)}
            for t, p in zip(times, prices) if p is not None
        ]
        return hourly
