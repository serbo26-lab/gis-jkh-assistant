"""Fee documents are separate from the house tariffs used in local calculations."""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

MIN_FEE_HELPER_VERSION = (0, 1, 34)
STATUSES = {"LOCAL": "Локальный проект", "DRAFT": "Проект в ГИС", "APPROVED": "Размещено",
            "TERMINATED": "Аннулировано"}


def helper_supports_fees(version):
    try:
        parts = tuple(int(part) for part in str(version).split("."))
        return len(parts) == 3 and parts >= MIN_FEE_HELPER_VERSION
    except ValueError:
        return False


def amount_text(value):
    try:
        amount = Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Укажите размер платы в рублях за 1 м² в месяц.") from exc
    if (not amount.is_finite() or amount <= 0 or amount >= Decimal("10000000000")
            or amount != amount.quantize(Decimal("0.01"))):
        raise ValueError("Размер платы должен быть больше нуля и содержать не более двух знаков после запятой.")
    return format(amount, ".2f")


def validate_document(document):
    """Validate only the recorded MANAGEMENT/BY_PROTOCOL workflow."""
    amount = amount_text(document.get("amount", ""))
    try:
        start, end = (date.fromisoformat(document[key]) for key in ("start", "end"))
    except (KeyError, ValueError) as exc:
        raise ValueError("Укажите начало и окончание периода платы.") from exc
    if end < start:
        raise ValueError("Окончание периода не может быть раньше его начала.")
    contract = document.get("contract") or {}
    if not all(contract.get(key) for key in ("guid", "versionGuid", "objectRootGuid", "fiasHouseGuid")):
        raise ValueError("Загрузите и выберите договор управления для этого дома.")
    for key, boundary in (("from", start), ("to", end)):
        value = contract.get(key)
        if value and ((key == "from" and boundary < date.fromisoformat(value))
                      or (key == "to" and boundary > date.fromisoformat(value))):
            def label(iso):
                return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else "не указано"
            raise ValueError(
                f"Период платы {label(document['start'])} — {label(document['end'])} "
                "выходит за срок управления домом по выбранному договору: "
                f"{label(contract.get('from'))} — {label(contract.get('to'))}. Проверьте даты."
            )
    protocol = document.get("protocol") or {}
    if not protocol.get("votingGuid") or not protocol.get("votingVersionGuid"):
        raise ValueError("Выберите протокол из ГИС. Без основания нельзя сохранить проект в ГИС.")
    if protocol.get("cancelled") or not protocol.get("eligible"):
        raise ValueError("Выбранный протокол недоступен как основание.")
    if contract["fiasHouseGuid"] not in protocol.get("fiasHouseCodes", []):
        raise ValueError("Выбранный протокол относится к другому дому.")
    return dict(document, amount=amount)


def document_from_remote(remote, contract):
    protocols = remote.get("ownerVotingList") or []
    # An absent registry protocol does not make the fee itself immutable.
    protocol = None
    if len(protocols) == 1:
        item = protocols[0]
        protocol = {"votingGuid": item.get("votingGuid"),
                    "votingVersionGuid": item.get("votingVersionGuid"),
                    "protocolCode": item.get("number", ""),
                    "protocolDate": item.get("date", ""),
                    "eligible": True, "cancelled": False,
                    "fiasHouseCodes": [contract["fiasHouseGuid"]]}
    return {"amount": remote.get("managementPayment", ""),
            "start": remote.get("period", {}).get("from", ""),
            "end": remote.get("period", {}).get("to", ""),
            "contract": contract, "protocol": protocol}


def can_edit_remote(remote):
    """A scan basis can be replaced; itemized/external fee data must be preserved."""
    return bool(remote and remote.get("status") in {"DRAFT", "APPROVED"}
                and remote.get("type") == "BY_PROTOCOL" and not remote.get("hasOtherDetails"))


def period_warning(document):
    contract = document.get("contract") or {}
    if ((contract.get("from") and document.get("start", "") < contract["from"])
            or (contract.get("to") and document.get("end", "") > contract["to"])):
        return "Период платы выходит за срок управления. Исправьте даты перед отправкой в ГИС."
    return ""


def fingerprint(document):
    contract, protocol = document.get("contract") or {}, document.get("protocol") or {}
    try:
        amount = amount_text(document.get("amount", ""))
    except ValueError:
        return None
    return (contract.get("guid"), contract.get("versionGuid"), contract.get("objectRootGuid"),
            document.get("start"), document.get("end"), amount,
            protocol.get("votingGuid"), protocol.get("votingVersionGuid"))


def initialize_fee_schema(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS management_fee_context (
            house_id TEXT PRIMARY KEY REFERENCES houses(id) ON DELETE CASCADE,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS management_fee_records (
            id TEXT PRIMARY KEY,
            house_id TEXT NOT NULL REFERENCES houses(id) ON DELETE CASCADE,
            remote_guid TEXT,
            document TEXT NOT NULL,
            remote TEXT,
            dirty INTEGER NOT NULL DEFAULT 1,
            pending INTEGER NOT NULL DEFAULT 0,
            UNIQUE(house_id, remote_guid)
        );
    """)


class ManagementFeeStore:
    def __init__(self, repository):
        self.repository = repository
        self.connection = repository.connection

    def context(self, house_id):
        row = self.connection.execute("SELECT value FROM management_fee_context WHERE house_id=?",
                                      (house_id,)).fetchone()
        return json.loads(row[0]) if row else {"contracts": [], "protocols": []}

    def save_context(self, house_id, value):
        with self.connection:
            self.connection.execute("INSERT INTO management_fee_context VALUES (?,?) "
                                    "ON CONFLICT(house_id) DO UPDATE SET value=excluded.value",
                                    (house_id, json.dumps(value, ensure_ascii=False)))

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        return dict(row) | {"document": json.loads(row["document"]),
                            "remote": json.loads(row["remote"]) if row["remote"] else None}

    def get(self, record_id):
        return self._decode(self.connection.execute(
            "SELECT * FROM management_fee_records WHERE id=?", (record_id,)).fetchone())

    def list(self, house_id):
        return [self._decode(row) for row in self.connection.execute(
            "SELECT * FROM management_fee_records WHERE house_id=? ORDER BY rowid DESC", (house_id,))]

    def save_local(self, house_id, document, record_id=None):
        if record_id:
            old = self.get(record_id)
            if not old or old["house_id"] != house_id:
                raise ValueError("Проект относится к другому дому.")
            remote = old["remote"]
            editable = (not remote or remote["status"] == "DRAFT"
                        or (remote["status"] == "APPROVED" and old["dirty"]))
            if old["pending"] or not editable:
                raise ValueError("Этот документ нельзя редактировать.")
            with self.connection:
                self.connection.execute("UPDATE management_fee_records SET document=?,dirty=1 WHERE id=?",
                                        (json.dumps(document, ensure_ascii=False), record_id))
        else:
            record_id = str(uuid.uuid4())
            with self.connection:
                self.connection.execute("INSERT INTO management_fee_records(id,house_id,document) VALUES (?,?,?)",
                                        (record_id, house_id, json.dumps(document, ensure_ascii=False)))
        return record_id

    def begin_published_edit(self, record_id):
        """An explicit local amendment, retaining the published optimistic-lock snapshot."""
        row = self.get(record_id)
        remote = row["remote"] if row else None
        if (not can_edit_remote(remote) or remote["status"] != "APPROVED" or row["pending"]):
            raise ValueError("Изменять можно только размещённые сведения с поддерживаемым основанием. Аннулированные сведения недоступны для изменения.")
        with self.connection:
            self.connection.execute("UPDATE management_fee_records SET dirty=1 WHERE id=?", (record_id,))

    def discard_local_edits(self, record_id):
        row = self.get(record_id)
        if not row or not row["remote"] or row["pending"]:
            raise ValueError("Сначала проверьте результат отправки в ГИС.")
        self.apply_remote(record_id, row["remote"], row["document"]["contract"])

    def mark_pending(self, record_id):
        with self.connection:
            self.connection.execute("UPDATE management_fee_records SET pending=1 WHERE id=?", (record_id,))

    def apply_remote(self, record_id, remote, contract):
        row = self.get(record_id)
        house = self.repository.get_house(row["house_id"]) if row else None
        if (not house or remote.get("agreementRootGuid") != contract.get("guid")
                or remote.get("managedObjectRootGuid") != contract.get("objectRootGuid")
                or house.fias_house_guid != contract.get("fiasHouseGuid")
                or not remote.get("guid") or remote.get("status") not in STATUSES):
            raise ValueError("Ответ ГИС не соответствует выбранному дому или договору.")
        if row["remote_guid"] and row["remote_guid"] != remote["guid"]:
            raise ValueError("Ответ ГИС относится к другой записи о плате.")
        previous = row["remote"] or {}
        if previous.get("status") == "TERMINATED" and remote.get("status") != "TERMINATED":
            raise ValueError("Аннулированная запись не может быть восстановлена. Загрузите актуальное состояние из ГИС.")
        document = document_from_remote(remote, contract)
        with self.connection:
            # A refresh may already have cached the result of an uncertain create.
            duplicate = next((item for item in self.list(house.id) if item["remote_guid"] == remote["guid"] and item["id"] != record_id), None)
            if duplicate and (duplicate["dirty"] or duplicate["pending"]):
                raise ValueError("Этот проект уже связан с локальными правками. Проверьте их перед объединением записей.")
            self.connection.execute("DELETE FROM management_fee_records WHERE house_id=? AND remote_guid=? AND id<>?",
                                    (house.id, remote["guid"], record_id))
            self.connection.execute("UPDATE management_fee_records SET remote_guid=?,remote=?,document=?,dirty=0,pending=0 WHERE id=?",
                                    (remote["guid"], json.dumps(remote, ensure_ascii=False),
                                     json.dumps(document, ensure_ascii=False), record_id))

    def import_remote(self, house_id, context):
        """Atomic refresh; do not overwrite local edits or forget vanished/history records."""
        contracts = {(c["versionGuid"], c["objectRootGuid"]): c for c in context["contracts"]}
        preserved = 0
        with self.connection.atomic():
            self.save_context(house_id, {"contracts": context["contracts"], "protocols": context["protocols"]})
            for envelope in context["records"]:
                remote = envelope["record"]
                contract = contracts.get((envelope["contractVersionGuid"], remote["managedObjectRootGuid"]))
                if contract is None:
                    raise ValueError("Ответ ГИС не соответствует выбранному дому или договору.")
                existing = next((r for r in self.list(house_id) if r["remote_guid"] == remote["guid"]), None)
                if existing and existing["dirty"] and remote.get("status") == "TERMINATED":
                    # Annulment is terminal. Keep unsent edits as a separate local
                    # project, never as a way to restore the annulled document.
                    self.save_local(house_id, existing["document"])
                    self.apply_remote(existing["id"], remote, contract)
                    preserved += 1
                    continue
                if existing and existing["dirty"]:
                    # A rejected/uncertain update must not discard the user's edited form.
                    if not existing["pending"] or fingerprint(existing["document"]) != fingerprint(document_from_remote(remote, contract)):
                        continue
                if not existing:
                    signature = fingerprint(document_from_remote(remote, contract))
                    candidates = [r for r in self.list(house_id) if r["pending"] and not r["remote_guid"]
                                  and signature is not None and fingerprint(r["document"]) == signature]
                    matches = [entry for entry in context["records"]
                               if entry["contractVersionGuid"] == contract["versionGuid"]
                               and entry["record"].get("managedObjectRootGuid") == contract["objectRootGuid"]
                               and signature is not None
                               and fingerprint(document_from_remote(entry["record"], contract)) == signature]
                    existing = candidates[0] if len(candidates) == 1 and len(matches) == 1 else None
                record_id = existing["id"] if existing else self.save_local(house_id, {})
                self.apply_remote(record_id, remote, contract)
        return preserved

    def delete_local(self, record_id):
        row = self.get(record_id)
        if not row or row["remote_guid"] or row["pending"]:
            raise ValueError("Можно удалить только локальный проект, который не отправлялся в ГИС.")
        with self.connection:
            self.connection.execute("DELETE FROM management_fee_records WHERE id=?", (record_id,))
