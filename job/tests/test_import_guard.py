from ci.check_imports import offenders


def test_no_scraper_imports_outside_sources():
    assert offenders() == []
