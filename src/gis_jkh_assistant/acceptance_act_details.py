"""Resolve the parties on the act date, without rewriting historical documents."""
from __future__ import annotations

import base64
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .database.act_requisites_store import ActRequisitesStore


def iso_date(text):
    value = str(text or "").strip()
    if not value:
        return ""
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Некорректная дата: {value}. Используйте ДД.ММ.ГГГГ.")


def display_date(text):
    return datetime.strptime(iso_date(text), "%Y-%m-%d").strftime("%d.%m.%Y") if text else ""


def suggested_genitive(full_name, gender=""):
    """Conservative local suggestion. The user can always save an exact form."""
    words = str(full_name).split()
    if len(words) != 3:
        return full_name
    female = gender == "female" or words[2].lower().endswith(("вна", "чна"))
    result = []
    for index, word in enumerate(words):
        lower = word.lower()
        irregular = {"Павел": "Павла", "Лев": "Льва", "Пётр": "Петра", "Петр": "Петра", "Илья": "Ильи", "Любовь": "Любови"}
        if index == 1 and word in irregular:
            converted = irregular[word]
        elif index == 0 and lower.endswith(("ко", "их", "ых", "о", "е", "и", "у", "ю")):
            converted = word
        elif female:
            if lower.endswith("ова") or lower.endswith("ева") or lower.endswith("ина"):
                converted = word[:-1] + "ой"
            elif lower.endswith("ская") or lower.endswith("цкая"):
                converted = word[:-2] + "ой"
            elif lower.endswith("ия"):
                converted = word[:-1] + "и"
            elif lower.endswith("а"):
                converted = word[:-1] + ("и" if lower[-2:-1] in "гкхжчшщ" else "ы")
            elif lower.endswith("я"):
                converted = word[:-1] + "и"
            else:
                converted = word
        elif index == 0 and lower.endswith(("ский", "цкий", "ой", "ый")):
            converted = word[:-2] + "ого"
        elif lower.endswith(("й", "ь")):
            converted = word[:-1] + "я"
        elif lower.endswith("а"):
            converted = word[:-1] + "ы"
        elif lower[-1:] in "бвгджзклмнпрстфхцчшщ":
            converted = word + "а"
        else:
            converted = word
        result.append(converted)
    return " ".join(result)


def _position_adjective_genitive(word):
    word = word.casefold()
    if word.endswith(("ый", "ой", "кий", "гий", "хий")):
        return word[:-2] + "ого"
    if word.endswith("ий"):
        return word[:-2] + "его"
    return None


def suggested_position_genitive(position):
    """Decline common job titles; leave unfamiliar constructions for manual input."""
    nouns = {"директор": "директора", "гендиректор": "гендиректора", "председатель": "председателя",
             "руководитель": "руководителя", "заместитель": "заместителя", "представитель": "представителя",
             "начальник": "начальника", "предприниматель": "предпринимателя", "член": "члена",
             "управляющий": "управляющего", "управляющая": "управляющей",
             "исполняющий": "исполняющего", "исполняющая": "исполняющей"}
    words = str(position or "").split()
    for index, word in enumerate(words):
        head = nouns.get(word.casefold())
        if head:
            adjectives = [_position_adjective_genitive(value) for value in words[:index]]
            if any(value is None for value in adjectives):
                return ""
            return " ".join([*adjectives, head, *words[index + 1:]])
    return ""


def has_quality_issues(item):
    if item.days_count:
        return True
    try:
        return Decimal(str(item.cost_reduction or "0").replace(",", ".")) != 0
    except InvalidOperation:
        return True


def fixation_for_act(item, rows):
    if item.work_list_item_id:
        matches = [row for row in rows if row.work_list_item_id == item.work_list_item_id]
        if len(matches) == 1:
            return matches[0]
    if item.work_guid:
        matches = [row for row in rows if row.work_guid == item.work_guid]
        if len(matches) == 1:
            return matches[0]
    if item.work_code:
        matches = [row for row in rows if row.work_code == item.work_code and
                   not (item.work_guid and row.work_guid and item.work_guid != row.work_guid)]
        if len(matches) == 1:
            return matches[0]
    return None


def address_parts(address):
    """Only recognize explicit locality/house markers; never remove bare numbers."""
    text = str(address or "").strip()
    segments = text.split(",")
    locality_markers = {"г", "г.", "город", "с", "с.", "село", "п", "п.", "пос", "пос.",
                        "поселок", "посёлок", "ст-ца", "станица"}
    annex_markers = {"корп", "корп.", "корпус", "стр", "стр.", "строение", "лит", "лит.", "литер"}
    result = {"locality": "", "house_number": "", "address_without_number": ""}
    for segment in segments:
        words = segment.split(maxsplit=1)
        if len(words) == 2 and words[0].casefold() in locality_markers:
            result["locality"] = segment.strip()
            break
    # Parse comma-separated suffixes once, from the end. A repeated regex over
    # all possible house/suffix boundaries can backtrack exponentially on input.
    suffixes = []
    for index in range(len(segments) - 1, -1, -1):
        segment = segments[index]
        words = segment.split(maxsplit=1)
        if len(words) == 2 and words[0].casefold() in annex_markers:
            suffixes.append(segment.strip())
            continue
        marker = re.search(r"(?<!\S)(?:д\.?|дом)\s+", segment, re.I)
        if marker and segment[marker.end():].strip():
            result["house_number"] = ", ".join([segment[marker.end():].strip(), *reversed(suffixes)])
            prefix = [*segments[:index], segment[:marker.start()]]
            result["address_without_number"] = ",".join(prefix).rstrip(", ")
        break
    return result


def basis_text(person):
    if person.get("basis_text"):
        return person["basis_text"]
    kind = person.get("basis_kind") or ""
    result = {"Устав": "Устава", "Доверенность": "доверенности",
              "Решение ОСС": "решения общего собрания собственников (протокол"}.get(kind, kind)
    if person.get("basis_number"):
        result += " № " + person["basis_number"]
    if person.get("basis_date"):
        result += " от " + display_date(person["basis_date"])
    if kind == "Решение ОСС":
        result += ")"
    return result


def resolve_act_details(repository, house_id, act_date, *, executor_id=None, customer_id=None):
    house = repository.get_house(house_id)
    if house is None:
        raise ValueError("Дом акта не найден.")
    organization = repository.get_organization(house.organization_id)
    store = ActRequisitesStore(repository)
    point = iso_date(act_date)
    issues = []

    def choose(owner, selected, label):
        candidates = store.people_on_date(point, **owner)
        if selected:
            candidates = [p for p in candidates if p["id"] == selected]
        if len(candidates) != 1:
            issues.append(("Не указан " if not candidates else "Выберите одного: ") + label + ".")
            return {}
        person = dict(candidates[0])
        person["name_genitive"] = person.get("name_genitive") or suggested_genitive(person["full_name"], person.get("gender"))
        person["position_genitive"] = person.get("position_genitive") or suggested_position_genitive(person.get("position"))
        person["basis"] = basis_text(person)
        if not person.get("position") and owner.get("organization_id"):
            issues.append("У подписанта УО не указана должность.")
        elif owner.get("organization_id") and not person["position_genitive"]:
            issues.append("Уточните должность подписанта УО в родительном падеже.")
        if not person.get("gender"):
            issues.append("Уточните согласование текста для подписанта: " + person["full_name"] + ".")
        if owner.get("house_id") and not person.get("premises"):
            issues.append("У представителя собственников не указан номер помещения.")
        if owner.get("house_id") and not person.get("role"):
            issues.append("Укажите статус представителя собственников.")
        if person.get("basis_kind") != "Устав" and not person.get("basis_text") and not person.get("basis_date"):
            issues.append("Не заполнены реквизиты основания полномочий: " + person["full_name"] + ".")
        image = person.pop("signature", None)
        person["signature_base64"] = base64.b64encode(image).decode("ascii") if image else None
        return person

    parts = address_parts(house.address)
    parts.update({key: value for key, value in store.house_fields(house.id).items() if value})
    for field, label in (("locality", "населённый пункт"), ("house_number", "номер дома"), ("address_without_number", "адрес без номера дома")):
        if not parts.get(field):
            issues.append("Уточните в карточке дома " + label + " для акта.")
    if not house.management_agreement_number or not house.management_agreement_date:
        issues.append("В карточке дома не заполнены номер и дата договора управления.")
    def checked_house_date(value, label):
        try:
            return iso_date(value)
        except ValueError:
            issues.append("Исправьте в карточке дома поле «" + label + "»: " + str(value) + ".")
            return ""

    agreement_date = checked_house_date(house.management_agreement_date, "Дата договора управления")
    start_date = checked_house_date(house.management_start_date, "Начало управления")
    end_date = checked_house_date(house.management_end_date, "Окончание управления")
    if agreement_date and agreement_date > point:
        issues.append("Сохранённый договор заключён позже даты акта. Проверьте реквизиты исторического периода.")
    if start_date and start_date > point:
        issues.append("По сохранённым сведениям управление началось позже даты акта.")
    if end_date and point >= end_date:
        issues.append("На дату акта управление уже окончено. Проверьте дату акта и договор соответствующего периода.")
    executor = choose({"organization_id": house.organization_id}, executor_id, "подписант УО на дату акта")
    customer = choose({"house_id": house.id}, customer_id, "представитель собственников на дату акта")
    options = store.options(house.organization_id)
    return dict(
        template_version="761pr-v1", organization_name=organization.name if organization else "",
        house_id=house.id, house_address=house.address, **parts,
        agreement_number=house.management_agreement_number or "",
        agreement_date=display_date(agreement_date),
        executor=executor, customer=customer, issues=issues,
        insert_images=bool(options["insert_images"]),
        customer_signature_placeholder=bool(options["customer_signature_placeholder"]),
        stamp_base64=base64.b64encode(options["stamp"]).decode("ascii") if options["stamp"] else None,
    )
