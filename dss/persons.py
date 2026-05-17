"""DSS Pro V8 person/face management.

Endpoints (подтверждены публичными интеграциями, см. подтверждённые источники
в коммите, где этот модуль появился):

  GET  /obms/api/v1.1/acs/person-group/list
  POST /obms/api/v1.1/acs/person          — создание персоны + загрузка лиц(а)
                                             как base64 в baseInfo.facePictures.

Кеш orgCode по имени держим в памяти процесса — для типичной школы их единицы
и редко меняются.
"""
from __future__ import annotations

import base64
from typing import Any

from loguru import logger

from .client import DSSClient


PERSON_GROUP_LIST_PATH = "/obms/api/v1.1/acs/person-group/list"
PERSON_CREATE_PATH = "/obms/api/v1.1/acs/person"
PERSON_LIST_PATH = "/obms/api/v1.1/acs/person/list"


class DSSPersonError(RuntimeError):
    pass


class DSSPersonClient:
    def __init__(self, dss: DSSClient) -> None:
        self.dss = dss
        # name → orgCode. Заполняется лениво при первом запросе.
        self._group_cache: dict[str, str] = {}
        # Полный плоский список с parent — для рекурсивного обхода
        # ([{orgCode, name, parent}]). parent=None для корневых узлов.
        self._tree_flat: list[dict] = []

    @staticmethod
    def _flatten_group_tree(
        nodes: Any, parent_code: str | None = None
    ) -> list[dict]:
        """DSS возвращает дерево групп; уплощаем в [{orgCode, name, parent}].
        parent — orgCode родителя (None для корней). Нужен для рекурсивного
        синка (Secondary Students → её подгруппы 5A/5B/...)."""
        out: list[dict] = []
        if not isinstance(nodes, list):
            return out
        for n in nodes:
            if not isinstance(n, dict):
                continue
            org = n.get("orgCode") or n.get("code")
            name = n.get("name") or n.get("orgName")
            if org and name:
                out.append({
                    "orgCode": str(org),
                    "name": str(name),
                    "parent": parent_code,
                })
            for child_key in ("children", "treeNodeList", "subList"):
                children = n.get(child_key)
                if children:
                    out.extend(DSSPersonClient._flatten_group_tree(
                        children, parent_code=str(org) if org else parent_code,
                    ))
        return out

    async def list_groups(self, force: bool = False) -> list[dict]:
        """Возвращает плоский список DSS person-групп: [{orgCode, name, parent}]."""
        if self._tree_flat and not force:
            return list(self._tree_flat)
        resp = await self.dss.request("GET", PERSON_GROUP_LIST_PATH)
        # Ответ обычно {code, data: {treeNodeList: [...]}} или {data: [...]}.
        data = resp.get("data") if isinstance(resp, dict) else None
        if isinstance(data, dict):
            roots = (
                data.get("treeNodeList")
                or data.get("list")
                or data.get("personGroupList")
                or []
            )
        elif isinstance(data, list):
            roots = data
        else:
            roots = []
        flat = self._flatten_group_tree(roots if isinstance(roots, list) else [roots])
        self._tree_flat = flat
        # Кэш name→orgCode оставляем для обратной совместимости. Если разные
        # группы имеют одинаковое имя на разных ветках — последняя побеждает
        # (DSS UI обычно не разрешает коллизии в одной системе).
        self._group_cache = {item["name"]: item["orgCode"] for item in flat}
        return flat

    async def get_org_code(self, group_name: str) -> str | None:
        """orgCode по имени группы. Сначала из кэша, потом перечитываем."""
        if group_name in self._group_cache:
            return self._group_cache[group_name]
        await self.list_groups(force=True)
        return self._group_cache.get(group_name)

    async def get_org_codes_recursive(self, group_name: str) -> list[str]:
        """orgCode группы + всех её потомков (BFS).

        Используется для синка: «Secondary Students» в DSS — это контейнер с
        классами-подгруппами (5A, 5B, ...), и сами ученики лежат в листьях.
        Запрос person/list по orgCode родителя обычно возвращает только тех,
        кто прямо в нём — без рекурсии. Поэтому мы собираем все orgCode-ы
        этого поддерева и ходим за списком персон по каждому.

        Возвращает [] если имя не найдено.
        """
        if not self._tree_flat:
            await self.list_groups(force=True)
        root = next(
            (n for n in self._tree_flat if n["name"] == group_name), None
        )
        if not root:
            return []
        result: list[str] = [root["orgCode"]]
        queue: list[str] = [root["orgCode"]]
        while queue:
            cur = queue.pop(0)
            for n in self._tree_flat:
                if n.get("parent") == cur:
                    result.append(n["orgCode"])
                    queue.append(n["orgCode"])
        return result

    async def find_person_groups(self, person_id: str) -> list[str]:
        """Возвращает имена DSS-групп, в которых состоит указанный personId.

        Прямой live-запрос к DSS — использовать как fallback, когда локальная
        person_groups ещё не успела синкнуть человека (например, человек
        только что создан в DSS, авто-синк ещё не прошёл, или его группа не
        в DSS_AUTO_SYNC_GROUPS).

        При ошибке DSS возвращает [] (без исключения), чтобы не валить
        вызывающий код — это диагностика, а не критический путь.
        """
        if not person_id:
            return []
        body = {"page": 1, "pageSize": 10, "personId": str(person_id)}
        try:
            resp = await self.dss.request("POST", PERSON_LIST_PATH, json=body)
        except Exception as e:
            logger.warning("find_person_groups({}): {}", person_id, e)
            return []
        if not isinstance(resp, dict):
            return []
        data = resp.get("data")
        items: list = []
        if isinstance(data, dict):
            items = (
                data.get("pageData")
                or data.get("list")
                or data.get("personList")
                or data.get("rows")
                or []
            )
        elif isinstance(data, list):
            items = data
        org_codes: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            # personId может быть в корне или в baseInfo
            pid_raw = (
                it.get("personId")
                or (it.get("baseInfo") or {}).get("personId")
            )
            if str(pid_raw) != str(person_id):
                # DSS иногда возвращает похожие совпадения, фильтруем точно
                continue
            oc = (
                it.get("orgCode")
                or (it.get("baseInfo") or {}).get("orgCode")
                or it.get("personGroupCode")
            )
            if oc and str(oc) not in org_codes:
                org_codes.append(str(oc))
        if not org_codes:
            return []
        if not self._tree_flat:
            await self.list_groups(force=True)
        code_to_name = {n["orgCode"]: n["name"] for n in self._tree_flat}
        return [code_to_name.get(oc, oc) for oc in org_codes]

    async def list_persons_in_group(
        self, org_code: str, page_size: int = 200, max_pages: int = 200,
    ) -> list[str]:
        """Возвращает personId всех людей группы. Пагинирует до конца.

        DSS Pro V8: POST /obms/api/v1.1/acs/person/list, тело
        {page, pageSize, personGroupCode}; ответ — {data: {pageData: [...],
        totalCount, ...}}. Имена полей варьируются между билдами, поэтому
        принимаем несколько алиасов.

        Защита от ложной чистки: если DSS вернул totalCount > 0, но мы не
        смогли распарсить ни одного personId — это подозрительно (скорее всего
        несовпадение формата ответа), бросаем DSSPersonError, чтобы вызывающий
        не интерпретировал результат как «группа пустая» и не стёр локальную.
        """
        out: list[str] = []
        seen: set[str] = set()
        reported_total: int | None = None
        for page in range(1, max_pages + 1):
            body = {
                "page": page,
                "pageSize": page_size,
                "personGroupCode": str(org_code),
            }
            resp = await self.dss.request("POST", PERSON_LIST_PATH, json=body)
            if not isinstance(resp, dict):
                raise DSSPersonError(f"DSS person/list: unexpected response: {resp!r}")
            code = resp.get("code")
            if code not in (1000, "1000", 0, "0", None, 200, "200"):
                raise DSSPersonError(
                    f"DSS person/list code={code} desc={resp.get('desc')!r}"
                )
            data = resp.get("data")
            page_items: list = []
            if isinstance(data, dict):
                page_items = (
                    data.get("pageData")
                    or data.get("list")
                    or data.get("personList")
                    or data.get("rows")
                    or []
                )
                if reported_total is None:
                    total_raw = data.get("totalCount") or data.get("total") or data.get("totals")
                    try:
                        reported_total = int(total_raw) if total_raw is not None else None
                    except (TypeError, ValueError):
                        reported_total = None
            elif isinstance(data, list):
                page_items = data
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                pid = (
                    item.get("personId")
                    or (item.get("baseInfo") or {}).get("personId")
                    or item.get("id")
                )
                if not pid:
                    continue
                pid_s = str(pid).strip()
                if pid_s and pid_s not in seen:
                    seen.add(pid_s)
                    out.append(pid_s)
            if not page_items:
                break
            if len(page_items) < page_size:
                break
            if reported_total is not None and len(out) >= reported_total:
                break
        if reported_total and reported_total > 0 and not out:
            raise DSSPersonError(
                f"DSS person/list: totalCount={reported_total} but parsed 0 personIds — "
                f"unexpected response shape, refusing to wipe local mirror"
            )
        return out

    @staticmethod
    def _encode_photo(photo_bytes: bytes) -> str:
        """JPG/PNG bytes → base64 без префикса data:."""
        return base64.b64encode(photo_bytes).decode("ascii")

    async def create_person(
        self,
        *,
        person_id: str,
        first_name: str,
        last_name: str = "",
        org_code: str,
        photo_bytes: bytes | None = None,
        phone: str | None = None,
        gender: str = "0",
    ) -> dict:
        """Создаёт персону в DSS. Все числовые-вид поля DSS ждёт строками.

        Возвращает тело ответа DSS. Бросает DSSPersonError если code != 1000.
        """
        face_pictures = [self._encode_photo(photo_bytes)] if photo_bytes else []
        body: dict[str, Any] = {
            "baseInfo": {
                "personId": str(person_id),
                "firstName": first_name,
                "lastName": last_name,
                "gender": str(gender),
                "orgCode": str(org_code),
                "source": "0",
                "facePictures": face_pictures,
            },
            "extensionInfo": {
                "idType": "0",
                "nationalityId": "9999",
            },
            # Без startTime/endTime DSS принимает persona без срока действия в одних
            # билдах, но валится в других. Ставим разумный диапазон: с эпохи и
            # до 2033-05-18 — типичный «вечный» паттерн в DSS-интеграциях.
            "authenticationInfo": {
                "startTime": "0",
                "endTime": "2000000000",
            },
            "accessInfo": {"accessType": "0"},
            "faceComparisonInfo": {"enableFaceComparisonGroup": "1"},
            "entranceInfo": {},
        }
        if phone:
            # Поле, которое отображается в DSS UI как «Телефон». Имя поля под
            # extensionInfo варьируется между билдами; кладём в оба варианта.
            body["extensionInfo"]["tel"] = phone
            body["baseInfo"]["tel"] = phone

        logger.debug("DSS person create: id={} name={} org={} face={}",
                     person_id, f"{first_name} {last_name}".strip(),
                     org_code, "yes" if face_pictures else "no")
        resp = await self.dss.request("POST", PERSON_CREATE_PATH, json=body)
        code = resp.get("code") if isinstance(resp, dict) else None
        if code not in (1000, "1000", 0, "0", None):
            # Некоторые DSS возвращают code=200 при успехе — оставляем None как ОК.
            msg = resp.get("desc") or resp.get("message") or str(resp)
            raise DSSPersonError(f"DSS person create failed (code={code}): {msg}")
        return resp if isinstance(resp, dict) else {}
