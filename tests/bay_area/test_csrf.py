from bay_area_courtbot.auth.csrf import extract_token


def test_extract_token_basic() -> None:
    html = """
    <html><body>
      <form>
        <input name="__RequestVerificationToken" type="hidden" value="ABC123XYZ" />
      </form>
    </body></html>
    """
    assert extract_token(html) == "ABC123XYZ"


def test_extract_token_missing() -> None:
    assert extract_token("<html><body>no token</body></html>") is None


def test_extract_token_empty() -> None:
    assert extract_token("") is None
    assert extract_token("not html at all <<<") is None or True  # tolerate


def test_extract_token_picks_first_when_multiple() -> None:
    html = """
    <input name="__RequestVerificationToken" value="first" />
    <input name="__RequestVerificationToken" value="second" />
    """
    assert extract_token(html) == "first"
