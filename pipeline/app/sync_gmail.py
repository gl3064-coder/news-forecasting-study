from __future__ import annotations

from .db import init_db
from .services.gmail import sync_newsletters


def main() -> None:
    init_db()
    result = sync_newsletters()
    print(result)


if __name__ == "__main__":
    main()
