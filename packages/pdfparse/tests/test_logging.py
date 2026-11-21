import io
import json
import logging
import time

from pdfparse.logging import (
    current_request_id,
    current_client_ip,
    current_user,
    get_request_id,
    get_client_ip,
    get_user,
    set_request_id,
    set_client_ip,
    set_user,
    setup_logging,
    shutdown_logging,
)


def test_structured_json_logging_with_request_id_ip_and_user():
    stream = io.StringIO()
    logger = setup_logging("test-service", level=logging.INFO, stream=stream)

    set_request_id("req-test-1234")
    set_client_ip("192.168.1.100")
    set_user("admin@corp.local")

    assert get_request_id() == "req-test-1234"
    assert get_client_ip() == "192.168.1.100"
    assert get_user() == "admin@corp.local"

    logger.info("Test message", extra={"duration_ms": 12.5, "status_code": 200, "custom_field": "val"})

    # Wait briefly for QueueListener to process
    time.sleep(0.05)
    shutdown_logging()

    output = stream.getvalue().strip()
    assert output, "Output should not be empty"

    data = json.loads(output)
    assert data["level"] == "INFO"
    assert data["service"] == "test-service"
    assert data["message"] == "Test message"
    assert data["request_id"] == "req-test-1234"
    assert data["client_ip"] == "192.168.1.100"
    assert data["user"] == "admin@corp.local"
    assert data["duration_ms"] == 12.5
    assert data["status_code"] == 200
    assert data["extra"]["custom_field"] == "val"
    assert "timestamp" in data
