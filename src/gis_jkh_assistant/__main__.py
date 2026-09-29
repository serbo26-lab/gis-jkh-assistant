"""Development entry point."""

from gis_jkh_assistant import __version__


def main() -> None:
    """Run the current development entry point."""
    print(f"GIS ЖКХ Assistant {__version__}")
    print("Следующий модуль: импорт объектов жилищного фонда.")


if __name__ == "__main__":
    main()
