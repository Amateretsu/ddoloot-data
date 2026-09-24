"""Unit tests for item_normalizer.exceptions."""

import pytest

from item_normalizer.exceptions import (
    ItemNormalizerError,
    NormalizationError,
    ParseError,
)


class TestParseError:
    def test_is_item_normalizer_error(self) -> None:
        err = ParseError("bad html")
        assert isinstance(err, ItemNormalizerError)

    def test_message(self) -> None:
        err = ParseError("bad html")
        assert str(err) == "bad html"

    def test_field_and_raw_value(self) -> None:
        err = ParseError("bad html", field="name", raw_value="<garbage>")
        assert err.field == "name"
        assert err.raw_value == "<garbage>"

    def test_defaults_are_none(self) -> None:
        err = ParseError("msg")
        assert err.field is None
        assert err.raw_value is None

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(ParseError, match="no wikitable"):
            raise ParseError("no wikitable", field="infobox")


class TestNormalizationError:
    def test_is_item_normalizer_error(self) -> None:
        err = NormalizationError("pydantic rejected")
        assert isinstance(err, ItemNormalizerError)

    def test_field_and_raw_value(self) -> None:
        err = NormalizationError("bad", field="minimum_level", raw_value="abc")
        assert err.field == "minimum_level"
        assert err.raw_value == "abc"

    def test_can_be_raised_and_caught_as_base(self) -> None:
        with pytest.raises(ItemNormalizerError):
            raise NormalizationError("pydantic rejected")
