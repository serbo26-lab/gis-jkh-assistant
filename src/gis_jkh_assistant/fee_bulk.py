"""Local tariff-derived fee projects and reviewed, house-scoped queue entries."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
import json

from .acceptance_act_details import iso_date
from .management_fees import amount_text, can_edit_remote, validate_document


@dataclass
class FeeCandidate:
    house_id: str
    address: str
    tariff_id: str
    document: dict
    detail: str = ""
    blocked: bool = False

    @property
    def key(self):
        return (self.house_id, self.tariff_id, self.document["start"], self.document["end"])


def _date(value):
    return date.fromisoformat(iso_date(value)) if value else None


def protocol_applies(protocol, fias):
    return bool(protocol and protocol.get("eligible") and not protocol.get("cancelled")
                and protocol.get("votingGuid") and protocol.get("votingVersionGuid")
                and fias and fias in protocol.get("fiasHouseCodes", []))


def available_protocols(repository, store, organization_id, house_ids):
    """Merge only identical GIS identities, never protocols with equal labels."""
    values = {}
    for house_id in house_ids:
        house = repository.get_house(house_id)
        if not house or house.organization_id != organization_id:
            raise ValueError("Дом не относится к выбранной УО.")
        for item in store.context(house_id)["protocols"]:
            if not protocol_applies(item, house.fias_house_guid):
                continue
            key = (item["votingGuid"], item["votingVersionGuid"])
            if key not in values:
                values[key] = deepcopy(item)
            else:
                values[key]["fiasHouseCodes"] = sorted(set(values[key]["fiasHouseCodes"]) | set(item["fiasHouseCodes"]))
    return list(values.values())


def _overlap_reason(store, house_id, start, end, contract=None):
    for record in store.list(house_id):
        if (record["remote"] or {}).get("status") == "TERMINATED":
            continue
        document = record["document"]
        if contract and document.get("contract") and document["contract"].get("guid") != contract.get("guid"):
            continue
        try:
            old_start, old_end = _date(document.get("start")), _date(document.get("end"))
        except ValueError:
            return "Сначала проверьте существующий проект с некорректным периодом."
        if old_start is None or old_end is None:
            return "Уже есть незаполненный проект. Проверьте его перед повторным формированием."
        if old_start <= end and start <= old_end:
            return "Уже есть проект или сведения за пересекающийся период. Существующая запись сохранена."
    return ""


def prepare_candidates(repository, store, organization_id, house_ids, start, end, protocol=None):
    if end < start:
        raise ValueError("Окончание периода не может быть раньше начала.")
    candidates = []
    for house_id in dict.fromkeys(house_ids):
        house = repository.get_house(house_id)
        if not house or house.organization_id != organization_id:
            raise ValueError("Дом не относится к выбранной УО.")
        lower, upper = start, end
        management_start, management_end = _date(house.management_start_date), _date(house.management_end_date)
        if management_start:
            lower = max(lower, management_start)
        if management_end:
            if management_end <= lower:
                continue
            upper = min(upper, management_end - timedelta(days=1))
        contracts = [item for item in store.context(house_id)["contracts"]
                     if item.get("fiasHouseGuid") == house.fias_house_guid]
        for tariff in repository.list_effective_house_tariffs(house_id):
            first = max(lower, tariff.effective_from_date)
            last = min(upper, tariff.effective_to_date or upper)
            if first > last:
                continue
            boundaries = {first, last + timedelta(days=1)} if last < date.max else {first}
            for contract in contracts:
                for value, increment in ((contract.get("from"), False), (contract.get("to"), True)):
                    boundary = _date(value)
                    if boundary == date.max and increment:
                        continue
                    if boundary and increment and boundary < date.max:
                        boundary += timedelta(days=1)
                    if boundary and first < boundary <= last:
                        boundaries.add(boundary)
            points = sorted(boundaries)
            for index, begin in enumerate(points):
                if begin > last:
                    continue
                finish = points[index + 1] - timedelta(days=1) if index + 1 < len(points) else last
                matches = [item for item in contracts
                           if (not item.get("from") or _date(item["from"]) <= begin)
                           and (not item.get("to") or _date(item["to"]) >= finish)]
                contract = deepcopy(matches[0]) if len(matches) == 1 else None
                selected_protocol = deepcopy(protocol) if protocol_applies(protocol, house.fias_house_guid) else None
                amount_error = ""
                try:
                    amount = amount_text(tariff.amount)
                except ValueError as exc:
                    amount, amount_error = tariff.amount, str(exc)
                document = {"amount": amount, "start": begin.isoformat(),
                            "end": finish.isoformat(), "contract": contract, "protocol": selected_protocol}
                reason = _overlap_reason(store, house_id, begin, finish, contract) or amount_error
                warnings = []
                if not contract:
                    warnings.append("Выберите договор" if matches else "Загрузите договоры из ГИС или выберите договор")
                if not selected_protocol:
                    warnings.append("Выберите протокол для этого дома")
                if contract and selected_protocol:
                    try:
                        validate_document(document)
                    except ValueError as exc:
                        warnings.append(str(exc))
                candidates.append(FeeCandidate(house_id, house.address, tariff.id, document,
                                               reason or "; ".join(warnings) or "Готов к сохранению в ГИС", bool(reason)))
    return candidates


def create_projects(repository, store, organization_id, house_ids, start, end, protocol, selected_candidates):
    """Recheck live data and create the reviewed selection in one transaction."""
    created = []
    with repository.connection.atomic():
        candidates = {item.key: item for item in prepare_candidates(
            repository, store, organization_id, house_ids, start, end, protocol)}
        seen = set()
        for reviewed in selected_candidates:
            candidate = candidates.get(reviewed.key)
            if (candidate is None or candidate.blocked or candidate.document != reviewed.document
                    or reviewed.key in seen):
                raise ValueError("Тарифы или проекты изменились. Обновите предварительный просмотр.")
            seen.add(reviewed.key)
            record_id = store.save_local(candidate.house_id, candidate.document)
            created.append((candidate.house_id, record_id))
    return created


def record_signature(record):
    return json.dumps((record["document"], record["remote_guid"],
                       (record["remote"] or {}).get("lastUpdate")), sort_keys=True, ensure_ascii=False)


def transfer_reason(repository, record, organization_id, action):
    house = repository.get_house(record["house_id"])
    if not house or house.organization_id != organization_id:
        return "Дом не относится к выбранной УО."
    if not house.fias_house_guid:
        return "Загрузите код ФИАС дома из ГИС."
    if record["pending"]:
        return "Результат прежней отправки не установлен. Сначала загрузите сведения из ГИС."
    remote = record["remote"] or {}
    if remote and (remote.get("status") == "TERMINATED" or
                   (remote.get("status") == "APPROVED" and not record["dirty"])):
        return "Сначала откройте размещённые сведения для изменения. Аннулированные записи не изменяются."
    if remote and not can_edit_remote(remote):
        return "Основание этой записи доступно только для просмотра."
    if action == "approve" and (remote.get("status") != "DRAFT" or record["dirty"]):
        return "Сначала сохраните проект и локальные правки в ГИС."
    if action == "save" and remote and not record["dirty"]:
        return "Проект уже сохранён в ГИС без локальных правок."
    try:
        document = validate_document(record["document"])
        if document["contract"]["fiasHouseGuid"] != house.fias_house_guid:
            return "Договор относится к другому дому."
    except (ValueError, KeyError, TypeError) as exc:
        return str(exc)
    return ""


@dataclass
class FeeJob:
    organization_id: str
    house_id: str
    address: str
    action: str
    record_id: str | None = None
    document: dict = field(default_factory=dict)
    signature: str = ""
    state: str = "ready"
    detail: str = ""
    fias: str | None = None
    unresolved: int = 0


def prepare_jobs(repository, store, organization_id, house_ids, action):
    if action not in {"load", "save", "approve", "save_approve"}:
        raise ValueError("Неизвестная операция с проектами платы.")
    jobs = []
    for house_id in dict.fromkeys(house_ids):
        house = repository.get_house(house_id)
        if not house or house.organization_id != organization_id:
            raise ValueError("Дом не относится к выбранной УО.")
        if action == "load":
            reason = "Загрузите код ФИАС дома." if not house.fias_house_guid else ""
            jobs.append(FeeJob(organization_id, house.id, house.address, action,
                               state="blocked" if reason else "ready", detail=reason, fias=house.fias_house_guid))
            continue
        for record in store.list(house_id):
            reason = transfer_reason(repository, record, organization_id, action)
            jobs.append(FeeJob(organization_id, house.id, house.address, action, record["id"],
                               deepcopy(record["document"]), record_signature(record),
                               "blocked" if reason else "ready", reason, house.fias_house_guid))
    return jobs


@dataclass
class FeeAmendment:
    record_id: str
    signature: str
    tariff_id: str | None
    document: dict
    detail: str = ""
    blocked: bool = False


def prepare_amendments(repository, store, organization_id, house_id, record_ids, protocol=None, choices=None):
    """One existing GIS identity -> one reviewed local tariff. Never split or guess."""
    house = repository.get_house(house_id)
    if not house or house.organization_id != organization_id:
        raise ValueError("Дом не относится к выбранной УО.")
    tariffs = repository.list_house_tariffs(house_id)
    choices = choices or {}
    result = []
    for record_id in dict.fromkeys(record_ids):
        record = store.get(record_id)
        if not record or record["house_id"] != house_id:
            raise ValueError("Выбранные сведения относятся к другому дому.")
        document = deepcopy(record["document"])
        remote = record["remote"] or {}
        reason = ""
        if record["pending"]:
            reason = "Сначала проверьте результат прежней отправки."
        elif remote and not can_edit_remote(remote):
            reason = "Эту запись нельзя изменять по тарифу; аннулированные сведения доступны только для просмотра."
        elif ((document.get("contract") or {}).get("fiasHouseGuid") not in (None, house.fias_house_guid)):
            reason = "Договор относится к другому дому."
        elif protocol is not None and not protocol_applies(protocol, house.fias_house_guid):
            reason = "Выбранный протокол недоступен или относится к другому дому."
        if record_id in choices:
            matched = next((t for t in tariffs if t.id == choices[record_id]), None)
        else:
            exact, overlaps = [], []
            for tariff in tariffs:
                first, last = tariff.effective_from_date.isoformat(), (tariff.effective_to_date or date.max).isoformat()
                if first == document.get("start") and last == document.get("end"):
                    exact.append(tariff)
                if first <= document.get("end", "") and document.get("start", "") <= last:
                    overlaps.append(tariff)
            candidates = exact or overlaps
            matched = candidates[0] if len(candidates) == 1 else None
        if not matched:
            reason = reason or "Выберите локальный тариф: подходящий период не определён однозначно."
        else:
            contract = document.get("contract") or {}
            first = max(matched.effective_from_date, _date(contract.get("from")) or date.min)
            # An open local tariff does not silently extend an existing GIS period.
            last = min(matched.effective_to_date or _date(document.get("end")) or date.max,
                       _date(contract.get("to")) or date.max)
            document.update(start=first.isoformat(), end=last.isoformat(), amount=matched.amount)
            if protocol is not None:
                document["protocol"] = deepcopy(protocol)
            if last < first:
                reason = reason or "Период тарифа не пересекается с выбранным договором."
            try:
                document["amount"] = amount_text(document["amount"])
            except ValueError as exc:
                reason = reason or str(exc)
        detail = reason or ("Готов к локальному изменению" if protocol_applies(document.get("protocol"), house.fias_house_guid)
                            else "Локальные правки; выберите протокол перед отправкой в ГИС")
        result.append(FeeAmendment(record_id, record_signature(record), matched.id if matched else None,
                                   document, detail, bool(reason)))
    # Compare final periods together, including unselected records of this contract.
    replacements = {item.record_id: item for item in result}
    records = store.list(house_id)
    for candidate in result:
        if candidate.blocked:
            continue
        doc = candidate.document
        for record in records:
            if record["id"] == candidate.record_id or (record["remote"] or {}).get("status") == "TERMINATED":
                continue
            replacement = replacements.get(record["id"])
            other = replacement.document if replacement and not replacement.blocked else record["document"]
            if ((other.get("contract") or {}).get("guid") == (doc.get("contract") or {}).get("guid")
                    and other.get("start", "") <= doc["end"] and doc["start"] <= other.get("end", "")):
                candidate.blocked = True
                candidate.detail = "Новый период пересекается с другой записью этого договора. Уточните соответствие тарифов."
                break
    return result


def apply_amendments(repository, store, organization_id, house_id, reviewed, protocol=None):
    """Retain remote GUID/version; update the approved selection atomically."""
    if not reviewed:
        return []
    if len({item.record_id for item in reviewed}) != len(reviewed):
        raise ValueError("Одна запись выбрана несколько раз.")
    with repository.connection.atomic():
        current = {item.record_id: item for item in prepare_amendments(
            repository, store, organization_id, house_id, [item.record_id for item in reviewed],
            protocol, {item.record_id: item.tariff_id for item in reviewed})}
        for item in reviewed:
            fresh = current[item.record_id]
            if fresh.blocked or fresh.signature != item.signature or fresh.document != item.document:
                raise ValueError("Сведения или тарифы изменились. Обновите предварительный просмотр.")
        for item in reviewed:
            record = store.get(item.record_id)
            if (record["remote"] or {}).get("status") == "APPROVED":
                store.begin_published_edit(item.record_id)
            store.save_local(house_id, item.document, item.record_id)
    return [item.record_id for item in reviewed]
