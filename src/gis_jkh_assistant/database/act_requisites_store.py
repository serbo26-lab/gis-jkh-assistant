"""Local signatory history and immutable source snapshots for acceptance acts."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

from gis_jkh_assistant.act_images import normalize_image_settings


PERSON_FIELDS = (
    "full_name", "name_genitive", "position", "position_genitive", "role", "premises", "gender",
    "basis_kind", "basis_number", "basis_date", "basis_text", "valid_from", "valid_to",
)
HOUSE_ACT_FIELDS = ("locality", "house_number", "address_without_number")


def initialize_act_schema(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS act_people (
            id TEXT PRIMARY KEY,
            organization_id TEXT REFERENCES management_organizations(id) ON DELETE CASCADE,
            house_id TEXT REFERENCES houses(id) ON DELETE CASCADE,
            data_json TEXT NOT NULL,
            signature BLOB,
            signature_name TEXT,
            CHECK ((organization_id IS NULL) != (house_id IS NULL))
        );
        CREATE INDEX IF NOT EXISTS ix_act_people_organization ON act_people(organization_id);
        CREATE INDEX IF NOT EXISTS ix_act_people_house ON act_people(house_id);
        CREATE TABLE IF NOT EXISTS act_house_requisites (
            house_id TEXT PRIMARY KEY REFERENCES houses(id) ON DELETE CASCADE,
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS act_organization_options (
            organization_id TEXT PRIMARY KEY REFERENCES management_organizations(id) ON DELETE CASCADE,
            insert_images INTEGER NOT NULL DEFAULT 0,
            customer_signature_placeholder INTEGER NOT NULL DEFAULT 0,
            stamp BLOB,
            stamp_name TEXT
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(act_organization_options)")}
    if "customer_signature_placeholder" not in columns:
        connection.execute("ALTER TABLE act_organization_options ADD COLUMN customer_signature_placeholder INTEGER NOT NULL DEFAULT 0")
    if "stamp_settings" not in columns:
        connection.execute("ALTER TABLE act_organization_options ADD COLUMN stamp_settings TEXT NOT NULL DEFAULT '{}'")
    people_columns = {row[1] for row in connection.execute("PRAGMA table_info(act_people)")}
    if "signature_settings" not in people_columns:
        connection.execute("ALTER TABLE act_people ADD COLUMN signature_settings TEXT NOT NULL DEFAULT '{}'")
    connection.commit()


def normalized_person(values, *, organization=True):
    result = {key: str(values.get(key) or "").strip() for key in PERSON_FIELDS}
    if not result["full_name"]:
        raise ValueError("Укажите ФИО подписанта.")
    if organization and not result["valid_from"]:
        raise ValueError("Укажите дату начала полномочий. Дата изменения записи в ГИС не является датой назначения.")
    for key in ("valid_from", "valid_to", "basis_date"):
        if result[key]:
            try:
                result[key] = date.fromisoformat(result[key]).isoformat()
            except ValueError as exc:
                raise ValueError("Проверьте даты полномочий и документа-основания.") from exc
    if result["valid_to"] and result["valid_to"] < result["valid_from"]:
        raise ValueError("Окончание полномочий не может быть раньше начала.")
    if organization and not result["basis_kind"]:
        raise ValueError("Укажите основание полномочий.")
    if result["gender"] not in {"", "male", "female"}:
        raise ValueError("Согласование укажите как male или female.")
    if not result["gender"]:
        patronymic = result["full_name"].split()[-1].lower()
        result["gender"] = "female" if patronymic.endswith(("вна", "чна")) else "male" if patronymic.endswith(("вич", "ьич")) else ""
    if result["basis_kind"] == "Иное" and not result["basis_text"]:
        raise ValueError("Для иного основания заполните его полный текст.")
    return result


class ActRequisitesStore:
    def __init__(self, repository):
        self.repository = repository
        self.connection = repository.connection

    def list_people(self, *, organization_id=None, house_id=None):
        if bool(organization_id) == bool(house_id):
            raise ValueError("Укажите одну УО или один дом.")
        field, value = ("organization_id", organization_id) if organization_id else ("house_id", house_id)
        rows = self.connection.execute(f"SELECT * FROM act_people WHERE {field} = ? ORDER BY rowid", (value,))
        return [dict(json.loads(row["data_json"]), id=row["id"],
                     organization_id=row["organization_id"], house_id=row["house_id"],
                     signature=row["signature"], signature_name=row["signature_name"],
                     signature_settings=json.loads(row["signature_settings"])) for row in rows]

    def save_person(self, values, *, organization_id=None, house_id=None, person_id=None):
        if bool(organization_id) == bool(house_id):
            raise ValueError("Укажите одну УО или один дом.")
        data = normalized_person(values, organization=bool(organization_id))
        if person_id:
            previous = self.connection.execute("SELECT * FROM act_people WHERE id = ?", (person_id,)).fetchone()
            if previous is None or previous["organization_id"] != organization_id or previous["house_id"] != house_id:
                raise ValueError("Подписант не относится к выбранному дому или УО.")
        else:
            for previous in self.list_people(organization_id=organization_id, house_id=house_id):
                if previous["full_name"].casefold() == data["full_name"].casefold() and previous["valid_from"] == data["valid_from"]:
                    raise ValueError("Такая запись с той же датой начала уже есть. Используйте «Изменить запись».")
            person_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute("""INSERT INTO act_people (id, organization_id, house_id, data_json)
                VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json""",
                (person_id, organization_id, house_id, json.dumps(data, ensure_ascii=False)))
        return person_id

    def people_on_date(self, on_date, **owner):
        point = date.fromisoformat(on_date).isoformat()
        return [person for person in self.list_people(**owner)
                if person["valid_from"] <= point and (not person["valid_to"] or point <= person["valid_to"])]

    def house_fields(self, house_id):
        row = self.connection.execute("SELECT data_json FROM act_house_requisites WHERE house_id=?", (house_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_house_fields(self, house_id, values):
        data = self.house_fields(house_id)
        data.update({key: str(value or "").strip() for key, value in values.items() if key in HOUSE_ACT_FIELDS})
        with self.connection:
            self.connection.execute("""INSERT INTO act_house_requisites VALUES (?, ?)
                ON CONFLICT(house_id) DO UPDATE SET data_json=excluded.data_json""",
                (house_id, json.dumps(data, ensure_ascii=False)))

    def options(self, organization_id):
        row = self.connection.execute("SELECT * FROM act_organization_options WHERE organization_id=?", (organization_id,)).fetchone()
        result = dict(row) if row else {"organization_id": organization_id, "insert_images": 0,
                                     "customer_signature_placeholder": 0,
                                     "stamp": None, "stamp_name": None, "stamp_settings": "{}"}
        result["stamp_settings"] = json.loads(result["stamp_settings"])
        return result

    def save_options(self, organization_id, **fields):
        previous = self.options(organization_id)
        previous.update({k: v for k, v in fields.items() if k in {
            "insert_images", "customer_signature_placeholder", "stamp", "stamp_name", "stamp_settings"}})
        if "stamp" in fields or "stamp_settings" in fields:
            previous["stamp_settings"] = normalize_image_settings(fields.get("stamp_settings"), kind="stamp")
        previous["stamp_settings"] = json.dumps(previous["stamp_settings"])
        with self.connection:
            self.connection.execute("""INSERT INTO act_organization_options
                (organization_id, insert_images, customer_signature_placeholder, stamp, stamp_name, stamp_settings)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(organization_id) DO UPDATE SET insert_images=excluded.insert_images,
                customer_signature_placeholder=excluded.customer_signature_placeholder,
                stamp=excluded.stamp, stamp_name=excluded.stamp_name, stamp_settings=excluded.stamp_settings""",
                tuple(previous[k] for k in ("organization_id", "insert_images", "customer_signature_placeholder", "stamp", "stamp_name", "stamp_settings")))

    def save_signature(self, person_id, content, name, settings=None):
        settings_json = json.dumps(normalize_image_settings(settings, kind="signature"))
        with self.connection:
            self.connection.execute("UPDATE act_people SET signature=?, signature_name=?, signature_settings=? WHERE id=?",
                                    (content, name, settings_json, person_id))

    def save_snapshot(self, act_id, payload):
        # The owning quality store creates this table alongside acts, before use.
        with self.connection:
            self.connection.execute("INSERT INTO acceptance_act_snapshots VALUES (?, ?, ?)",
                (act_id, json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def snapshot(self, act_id):
        row = self.connection.execute("SELECT payload_json FROM acceptance_act_snapshots WHERE act_id=?", (act_id,)).fetchone()
        return json.loads(row[0]) if row else None
