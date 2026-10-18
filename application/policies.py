"""Named execution limits shared by entry points and the crawl use case."""

MAX_CRAWL_WORKERS = 16


def validate_worker_count(workers: int) -> None:
    if type(workers) is not int or not 1 <= workers <= MAX_CRAWL_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_CRAWL_WORKERS}.")
