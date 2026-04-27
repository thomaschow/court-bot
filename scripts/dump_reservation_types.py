"""Hit GetAvailableReservationTypes with the params the modal uses, and print the list."""

import asyncio

from courtbot.auth.session import build_client
from courtbot.config import load_config
from courtbot.paths import config_path


async def main() -> None:
    cfg = load_config(config_path())
    f = cfg.facility("santa-clara")

    params = {
        "customSchedulerId": str(f.s_id),
        "userId": str(f.member_id),
        "startTime": "09:00:00",
        "date": "5/3/2026 12:00:00 AM",
        "courtId": "",
        "courtType": "2",
        "endTime": "10:00 AM",
        "isDynamicSlot": "False",
        "instructorId": "",
    }

    async with build_client(f, http2=False) as client:
        url = f"/Online/AjaxReservation/GetAvailableReservationTypes/{f.org_id}"
        r = await client.get(url, params=params)
        print(f"GET {url} -> {r.status_code}")
        print(f"body length: {len(r.text)}")
        try:
            data = r.json()
        except Exception:
            print(r.text[:500])
            return
        if isinstance(data, dict) and "Data" in data:
            items = data["Data"]
        else:
            items = data
        for it in items:
            if isinstance(it, dict):
                print(f"  Id={it.get('Id'):>8}  DisplayName={it.get('DisplayName')!r:30}  Name={it.get('Name')!r}")


if __name__ == "__main__":
    asyncio.run(main())
