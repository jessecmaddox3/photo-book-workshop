import json
from pathlib import Path
from PIL import Image
import pytest
from photobook_workshop.api_batch import (
    validate_config,
    build_batch_request,
    parse_results,
    quality_hash,
    reserve_micros,
    usage_cost,
)


def config(**changes):
    return validate_config(
        {
            "enabled": False,
            "model": "invented-vision-model",
            "pricing": {
                "input": "1.00",
                "cached_input": "0.10",
                "output": "2.00",
                "verified_on": "2030-01-01",
                "source": "Invented test rates, not a real model price",
            },
            "ceiling_usd": "1.00",
            "input_allowance": 4000,
            "max_output_tokens": 1200,
            **changes,
        }
    )


def test_pixels_only_default_and_explicit_metadata_policy(tmp_path):
    path = tmp_path / "preview.jpg"
    Image.new("RGB", (20, 20), "blue").save(path)
    item = {
        "url": "https://example.invalid/private-photo",
        "preview": str(path),
        "people": ["Invented Person"],
        "latitude": 0,
        "longitude": 0,
        "album": "Invented Album",
    }
    req = build_batch_request(item, config())
    s = json.dumps(req)
    assert (
        "private-photo" not in s
        and "Invented Person" not in s
        and "Invented Album" not in s
    )
    opted = build_batch_request(item, config(metadata_fields=["people"]))
    assert "Invented Person" in json.dumps(
        opted
    ) and "Invented Album" not in json.dumps(opted)
    assert req["body"]["store"] is False and req["body"]["max_output_tokens"] == 1200


@pytest.mark.parametrize(
    "changes",
    [
        {"ceiling_usd": "NaN"},
        {"ceiling_usd": "-1"},
        {"input_allowance": 0},
        {"max_output_tokens": True},
        {"pricing": {"input": "Infinity"}},
        {"enabled": "yes"},
        {"metadata_fields": ["raw_metadata_json"]},
    ],
)
def test_invalid_budget_and_policy_rejected(changes):
    with pytest.raises(ValueError):
        config(**changes)


def test_exact_reservation_and_unknown_usage_never_becomes_free():
    c = config()
    assert reserve_micros(c) == 6400
    assert usage_cost({"total_tokens": 123}, c["pricing"]) is None
    assert (
        usage_cost(
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 50},
            },
            c["pricing"],
        )
        == 95
    )
    assert (
        usage_cost(
            {"input_tokens": 100, "output_tokens": 20, "total_tokens": 119},
            c["pricing"],
        )
        is None
    )
    assert (
        usage_cost(
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 101},
            },
            c["pricing"],
        )
        is None
    )


def test_smoke_quality_binding_changes_for_model_or_metadata_but_not_rates():
    first = config()
    other = config()
    other["pricing"]["input"] = "9"
    assert quality_hash(first) == quality_hash(other)
    assert quality_hash(first) != quality_hash(config(model="another-invented-model"))
    assert quality_hash(first) != quality_hash(config(metadata_fields=["people"]))


def test_results_reject_foreign_duplicate_and_malformed_before_projection():
    expected = {"photo-one": "https://example.invalid/one"}
    record = {"custom_id": "photo-one", "error": {"code": "invented-failure"}}
    for body in [
        json.dumps({**record, "custom_id": "foreign"}),
        json.dumps(record) + "\n" + json.dumps(record),
        "{broken",
    ]:
        with pytest.raises(ValueError):
            parse_results(body.encode(), expected, config())
    result = parse_results(b"", expected, config())
    assert result[0]["outcome"] == "retry" and result[0]["cost_micros"] is None
