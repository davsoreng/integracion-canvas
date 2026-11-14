import sys
from src.core.logger import logger
from src.services.sync_manager import SyncManager


def main() -> int:
    """Punto de entrada principal para la sincronización directa (GitHub Actions o ejecución local)."""
    manager = SyncManager()
    response = manager.run_sync()

    if not response.success and response.stats.courses_processed == 0:
        logger.error("La sincronización finalizó sin poder procesar ningún curso.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
