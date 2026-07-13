"""Build HH.ru vacancy search URLs from job preferences."""

from urllib.parse import urlencode, quote

# HH.ru area codes for major cities
_AREA = {
    "москва": "1", "moscow": "1",
    "санкт-петербург": "2", "спб": "2", "saint-petersburg": "2",
    "екатеринбург": "3",
    "новосибирск": "4",
    "казань": "88",
    "remote": "1",  # HH treats remote as Moscow area + schedule filter
}

_SCHEDULE = {
    "remote": "remote",
    "hybrid": None,   # flexible removed as default — pass flexible=True for gig/temporary roles
    "office": None,
}


def build_hh_url(role: str, city: str = "Москва",
                 salary: str = "", remote: str = "hybrid",
                 search_scope: str = "everywhere",
                 flexible: bool = False) -> str:
    """Build HH.ru search URL.

    search_scope:
      "name"       — vacancy title only (precise, fewer results)
      "everywhere" — title + description + company (broad, LLM scorer filters precision)
    flexible:
      True  — add schedule=flexible (gig/temporary roles; cuts ~95% of listings)
      False — no schedule filter (default)
    """
    city_key = city.lower().strip()
    area = _AREA.get(city_key)  # None for any city not in the map — see below, don't guess

    params: dict = {
        "text": role,
        "search_field": search_scope if search_scope in ("name", "everywhere") else "everywhere",
    }
    if area is not None:
        params["area"] = area
    # else: omit area entirely rather than defaulting to Moscow. Verified live (2026-07-13) that
    # dropping the param searches across all HH regions (Russia + CIS network), not just Moscow —
    # a silent wrong-city match was worse than a broader-than-intended one. _AREA only covers 5
    # named cities; expanding it into a real region picker (or reading the account's own region)
    # is separate future work, not part of this fix — see L2_tasks.md.

    schedule = "flexible" if flexible else _SCHEDULE.get(remote.lower())
    if schedule:
        params["schedule"] = schedule

    if salary:
        try:
            params["salary"] = int(salary.replace(" ", "").replace(" ", ""))
            params["only_with_salary"] = "true"
        except ValueError:
            pass

    return "https://hh.ru/search/vacancy?" + urlencode(params)
