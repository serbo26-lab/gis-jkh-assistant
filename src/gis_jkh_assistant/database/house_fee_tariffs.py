"""Exact GIS tariff intervals, kept separately from the user's change points."""
from datetime import date
import json

from gis_jkh_assistant.management_fees import amount_text
from .models import HouseTariff


def initialize_schema(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS house_fee_tariff_snapshots (
        house_id TEXT PRIMARY KEY REFERENCES houses(id) ON DELETE CASCADE,
        value TEXT NOT NULL
    )""")
    connection.commit()


def save_snapshot(repository, house_id, context):
    """Validate a complete house response before replacing only its GIS snapshot."""
    house = repository.get_house(house_id)
    if not house or not house.fias_house_guid:
        raise ValueError("У дома нет кода ФИАС. Загрузите сведения о доме.")
    contracts = {(item["versionGuid"], item["objectRootGuid"]): item for item in context["contracts"]}
    if any(item.get("fiasHouseGuid") != house.fias_house_guid for item in contracts.values()):
        raise ValueError("В ответе ГИС есть договор другого дома.")
    values = []
    seen = set()
    for entry in context["records"]:
        record = entry["record"]
        if record.get("status") != "APPROVED":
            continue
        contract = contracts.get((entry["contractVersionGuid"], record["managedObjectRootGuid"]))
        if not contract or contract.get("guid") != record.get("agreementRootGuid"):
            raise ValueError("Сведения о плате не соответствуют договору выбранного дома.")
        start, end = (date.fromisoformat(record["period"][key]) for key in ("from", "to"))
        if end < start:
            raise ValueError("ГИС вернула неверный период платы.")
        if ((contract.get("from") and start < date.fromisoformat(contract["from"]))
                or (contract.get("to") and end > date.fromisoformat(contract["to"]))):
            raise ValueError("Период платы выходит за срок управления по договору ГИС.")
        guid = record["guid"]
        if not guid or guid in seen:
            raise ValueError("В ответе ГИС повторяются сведения о плате. Проверьте загрузку.")
        seen.add(guid)
        # Totals can be read even if the basis is not editable in the desktop.
        basis = "; ".join(f"№ {p.get('number', '')} от {p.get('date', '')}"
                          for p in record.get("ownerVotingList", [])) or "Другое основание"
        values.append(dict(guid=guid, start=start.isoformat(), end=end.isoformat(),
                           amount=amount_text(record["managementPayment"]), basis=basis))
    repository.connection.execute(
        "INSERT INTO house_fee_tariff_snapshots VALUES (?,?) "
        "ON CONFLICT(house_id) DO UPDATE SET value=excluded.value",
        (house_id, json.dumps(dict(fias=house.fias_house_guid, organization_id=house.organization_id,
                                   records=values), ensure_ascii=False)))
    return len(values)


def _records(repository, house_id):
    row = repository.connection.execute(
        "SELECT value FROM house_fee_tariff_snapshots WHERE house_id=?", (house_id,)).fetchone()
    snapshot = json.loads(row[0]) if row else {}
    house = repository.get_house(house_id)
    if (not house or snapshot.get("fias") != house.fias_house_guid
            or snapshot.get("organization_id") != house.organization_id):
        return []
    return snapshot.get("records", [])


def visible_tariffs(repository, house_id, local):
    """Overlay GIS intervals only where no local tariff is effective.

    Conflicting GIS amounts stay in the fee section for review; neither is
    guessed. Equal overlaps are one interval with a deterministic source.
    Ordinals allow the inclusive 31.12.9999 boundary without overflowing date.
    """
    remote = _records(repository, house_id)
    if not remote:
        return local
    intervals = [(date.fromisoformat(item["start"]).toordinal(),
                  date.fromisoformat(item["end"]).toordinal() + 1, item) for item in remote]
    protected = [(item.effective_from_date.toordinal(),
                  (item.effective_to_date or date.max).toordinal() + 1) for item in local]
    boundaries = sorted({point for first, last, *_ in (*intervals, *protected) for point in (first, last)})
    segments = []
    for first, last in zip(boundaries, boundaries[1:]):
        if any(begin <= first < end for begin, end in protected):
            continue
        active = [item for begin, end, item in intervals if begin <= first < end]
        if not active or len({item["amount"] for item in active}) != 1:
            continue
        item = min(active, key=lambda value: value["guid"])
        if segments and segments[-1][1] == first and segments[-1][2] == item:
            segments[-1] = (segments[-1][0], last, item)
        else:
            segments.append((first, last, item))
    result = list(local)
    for begin, finish, item in segments:
        start, end = date.fromordinal(begin), date.fromordinal(finish - 1)
        result.append(HouseTariff(
            id=f"gis-fee:{item['guid']}:{begin}", house_id=house_id, amount=item["amount"],
            start_year=start.year, start_month=start.month, start_day=start.day, start_precision="day",
            end_year=end.year, end_month=end.month, end_day=end.day,
            gis_fee_guid=item["guid"], basis_label=item["basis"]))
    return sorted(result, key=lambda item: item.effective_from_date)


def conflicting_periods(repository, house_id):
    records = _records(repository, house_id)
    return sum(a["start"] <= b["end"] and b["start"] <= a["end"] and a["amount"] != b["amount"]
               for index, a in enumerate(records) for b in records[index + 1:])
