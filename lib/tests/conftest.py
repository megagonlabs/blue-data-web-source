def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: integration tests — mocked HTTP, real trafilatura"
    )
