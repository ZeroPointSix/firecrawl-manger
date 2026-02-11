from __future__ import annotations

import json
import sys
from datetime import datetime

from app.config import load_config
from app.db.cleanup import cleanup_retention
from app.db.session import create_engine_from_config, create_session_factory


def main() -> int:
    config, _ = load_config()
    engine = create_engine_from_config(config)
    SessionLocal = create_session_factory(engine)

    with SessionLocal() as db:
        result = cleanup_retention(db, config=config)

    payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "request_logs_deleted": result.request_logs_deleted,
        "audit_logs_deleted": result.audit_logs_deleted,
        "idempotency_records_deleted": result.idempotency_records_deleted,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
