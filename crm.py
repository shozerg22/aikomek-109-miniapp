import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)

DEFAULT_CATEGORY_ALIASES = {
    "водоснабжение": "Водоснабжение города",
    "сумен жабдықтау": "Водоснабжение города",
    "канализация": "Водоотведение",
    "кәріз": "Водоотведение",
    "электроснабжение": "Электроснабжение города",
    "электрмен жабдықтау": "Электроснабжение города",
    "отопление": "Теплоснабжение города",
    "жылумен жабдықтау": "Теплоснабжение города",
    "вывоз мусора": "Твердые бытовые отходы",
    "қоқыс шығару": "Твердые бытовые отходы",
    "дороги": "Дорожная инфраструктура",
    "жолдар": "Дорожная инфраструктура",
    "благоустройство двора": "Благоустройство городской среды",
    "ауланы абаттандыру": "Благоустройство городской среды",
    "другое": "Вопросы по службе ikomek 109",
    "басқа": "Вопросы по службе ikomek 109",
}

DEFAULT_DISTRICT_ALIASES = {
    "көкшетау қаласы": "г. Кокшетау", "көкшетау қ": "г. Кокшетау",
    "қосшы қаласы": "г. Косшы", "қосшы қ": "г. Косшы",
    "степногорск қаласы": "г. Степногорск", "степногорск қ": "г. Степногорск",
    "щучинск қаласы": "г. Щучинск", "щучинск қ": "г. Щучинск",
    "ақкөл ауданы": "Аккольский район", "аршалы ауданы": "Аршалынский район",
    "астрахан ауданы": "Астраханский район", "атбасар ауданы": "Атбасарский район",
    "біржан сал ауданы": "район Биржан сал", "бұланды ауданы": "Буландынский район",
    "бурабай ауданы": "Бурабайский район", "егіндікөл ауданы": "Егиндыкольский район",
    "ерейментау ауданы": "Ерейментауский район", "есіл ауданы": "Есильский район",
    "жақсы ауданы": "Жаксынский район", "жарқайын ауданы": "Жаркаинский район",
    "зеренді ауданы": "Зерендинский район", "қорғалжын ауданы": "Коргалжынский район",
    "сандықтау ауданы": "Сандыктауский район", "целиноград ауданы": "Целиноградский район",
    "шортанды ауданы": "Шортандинский район",
}


class CRMError(RuntimeError):
    pass


@dataclass(frozen=True)
class CRMResult:
    appeal_id: int
    number: str
    status: str


def _clean_name(value: str) -> str:
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())


class CRMClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("CRM_BASE_URL", "").strip().rstrip("/")
        self.token = os.getenv("CRM_API_TOKEN", "").strip()
        self.organization_id = os.getenv("CRM_ORGANIZATION_ID", "").strip()
        self.status_id = int(os.getenv("CRM_DEFAULT_STATUS_ID", "1"))
        self.priority = os.getenv("CRM_DEFAULT_PRIORITY", "medium").strip() or "medium"
        self.timeout = float(os.getenv("CRM_TIMEOUT_SECONDS", "15"))
        raw_mapping = os.getenv("CRM_CATEGORY_MAP", "{}").strip() or "{}"
        try:
            self.category_map = json.loads(raw_mapping)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CRM_CATEGORY_MAP must be valid JSON") from exc
        routes_path = os.getenv("CRM_ROUTES_FILE", "").strip()
        self.routes_path = Path(routes_path) if routes_path else Path(__file__).with_name("crm_routes.json")
        self.route_data: dict[str, Any] = {}
        if self.routes_path.exists():
            self.route_data = json.loads(self.routes_path.read_text(encoding="utf-8"))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token and (self.organization_id or self.route_data))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
                async with session.request(method, url, **kwargs) as response:
                    body = await response.text()
                    if response.status >= 400:
                        raise CRMError(f"CRM returned HTTP {response.status}: {body[:500]}")
                    return json.loads(body) if body else {}
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise CRMError(f"CRM connection failed: {exc}") from exc

    async def categories(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "categories/")
        return result.get("results", result) if isinstance(result, dict) else result

    async def organizations(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "organizations/")
        return result.get("results", result) if isinstance(result, dict) else result

    async def resolve_category_id(self, category_name: str) -> int:
        category_name = self._alias("category_aliases", category_name)
        mapped = self.category_map.get(category_name) or self.category_map.get(_clean_name(category_name))
        if mapped:
            return int(mapped)

        wanted = _clean_name(category_name)
        for category in await self.categories():
            candidate = _clean_name(str(category.get("name", "")))
            if candidate == wanted or candidate in wanted or wanted in candidate:
                return int(category["id"])
        raise CRMError(f"CRM category is not mapped: {category_name}")

    def _alias(self, section: str, value: str) -> str:
        aliases = self.route_data.get(section, {})
        key = _clean_name(value)
        defaults = DEFAULT_CATEGORY_ALIASES if section == "category_aliases" else DEFAULT_DISTRICT_ALIASES
        return str(aliases.get(key) or defaults.get(key) or value)

    async def resolve_organization_id(self, district: str, category: str) -> int:
        route_names = self.route_candidates(district, category)
        district = self._alias("district_aliases", district)
        category = self._alias("category_aliases", category)
        if route_names:
            organizations = await self.organizations()
            by_name = {_clean_name(str(item.get("name", ""))): item for item in organizations}
            for name in route_names:
                match = by_name.get(_clean_name(name))
                if match:
                    return int(match["id"])
            raise CRMError(f"CRM route organizations are inactive or missing: {route_names}")

        fallback_names = self.district_fallback_candidates(district)
        if fallback_names:
            organizations = await self.organizations()
            by_name = {_clean_name(str(item.get("name", ""))): item for item in organizations}
            for name in fallback_names:
                match = by_name.get(_clean_name(name))
                if match:
                    logger.warning(
                        "No exact route for %s / %s; using district ЖКХ: %s",
                        district,
                        category,
                        name,
                    )
                    return int(match["id"])

        if self.organization_id:
            logger.warning("No route for %s / %s; using fallback organization", district, category)
            return int(self.organization_id)
        raise CRMError(f"CRM route is not configured: {district} / {category}")

    def district_fallback_candidates(self, district: str) -> list[str]:
        district = self._alias("district_aliases", district)
        wanted_district = _clean_name(district)
        names: list[str] = []
        for route in self.route_data.get("routes", []):
            if _clean_name(str(route.get("district", ""))) != wanted_district:
                continue
            organization = str(route.get("organization", "")).strip()
            if organization and _clean_name(organization).startswith("жкх ") and organization not in names:
                names.append(organization)
        return names

    def route_candidates(self, district: str, category: str) -> list[str]:
        district = self._alias("district_aliases", district)
        category = self._alias("category_aliases", category)
        wanted_district = _clean_name(district)
        wanted_category = _clean_name(category)
        route_names: list[str] = []
        for route in self.route_data.get("routes", []):
            if (
                _clean_name(str(route.get("district", ""))) == wanted_district
                and _clean_name(str(route.get("category", ""))) == wanted_category
                and route.get("organization")
            ):
                name = str(route["organization"])
                if name not in route_names:
                    route_names.append(name)
        return route_names

    async def create_appeal(self, appeal: dict[str, Any]) -> CRMResult:
        if not self.configured:
            raise CRMError("CRM integration is not configured")
        category_id = await self.resolve_category_id(appeal["category"])
        organization_id = await self.resolve_organization_id(appeal["district"], appeal["category"])
        district_name = self._alias("district_aliases", appeal["district"])
        payload = {
            "applicant_phone": appeal["applicant_phone"],
            "applicant_name": appeal.get("applicant_name", ""),
            "source": "telegram",
            "category": category_id,
            "description": appeal["description"],
            "priority": self.priority,
            "status": self.status_id,
            "organization": organization_id,
            "district": district_name,
            "street": appeal["address"],
            "telegram_chat_id": str(appeal["telegram_user_id"]),
        }
        result = await self._request("POST", "appeals/", json=payload)
        return CRMResult(
            appeal_id=int(result["id"]),
            number=str(result.get("number", result["id"])),
            status=str(result.get("status_name", result.get("status", "sent"))),
        )

    async def get_appeal(self, crm_id: int) -> dict[str, Any]:
        return await self._request("GET", f"appeals/{crm_id}/")

    async def update_appeal(self, crm_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", f"appeals/{crm_id}/", json=changes)
