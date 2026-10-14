"""Fictional, complete act requisites shared by document integrity scenarios."""
from gis_jkh_assistant.database.act_requisites_store import ActRequisitesStore


def populate_act_requisites(repository, work_list):
    house = repository.get_house(work_list.house_id)
    repository.update_house(house.id, management_agreement_number="TEST-1",
                            management_agreement_date="2020-01-01")
    store = ActRequisitesStore(repository)
    store.save_house_fields(house.id, dict(locality="г. Тестовый", house_number="1",
                                           address_without_number="г. Тестовый, ул. Учебная"))
    executor = store.save_person(dict(full_name="Иванов Иван Иванович", name_genitive="Иванова Ивана Ивановича",
        position="Директор", role="Руководитель", gender="male", valid_from="2020-01-01", basis_kind="Устав"),
        organization_id=house.organization_id)
    customer = store.save_person(dict(full_name="Петрова Мария Ивановна", name_genitive="Петровой Марии Ивановны",
        role="Председатель совета МКД", premises="7", gender="female", valid_from="2020-01-01",
        basis_kind="Решение ОСС", basis_number="1", basis_date="2020-01-01"), house_id=house.id)
    return executor, customer
