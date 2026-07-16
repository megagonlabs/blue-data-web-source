def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: integration tests — mocked HTTP, real trafilatura"
    )
    config.addinivalue_line(
        "markers", "e2e: end-to-end scenario tests — full stack with MemoryVectorSource and DummyEmbeddingModel"
    )
