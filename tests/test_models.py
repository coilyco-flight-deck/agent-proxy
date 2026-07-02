"""Logical-model registry flat-dict API (leg 04 step 2)."""

import pytest

from app.models import (
    FAST_THINK_NUM_CTX,
    CONSERVATIVE_NUM_CTX,
    LOGICAL_MODELS,
    get_model,
    list_models,
)

# The names this step seeds, with their expected safe num_ctx.
SEEDED = {
    "fast-think": FAST_THINK_NUM_CTX,
    "fast": CONSERVATIVE_NUM_CTX,
    "ctx-think": CONSERVATIVE_NUM_CTX,
    "ctx": CONSERVATIVE_NUM_CTX,
    "tune": CONSERVATIVE_NUM_CTX,
}


def test_fast_think_num_ctx_is_49152():
    """The headline acceptance: fast-think rides the leg-01-proven ceiling."""
    assert get_model("fast-think")["num_ctx"] == 49152


@pytest.mark.parametrize("name,num_ctx", SEEDED.items())
def test_seeded_models_present_with_expected_num_ctx(name, num_ctx):
    model = get_model(name)
    assert model is not None, f"{name} missing from registry"
    assert model["num_ctx"] == num_ctx
    # The flat view is exactly the three-key contract this step defines.
    assert set(model) == {"backend_url", "ollama_tag", "num_ctx"}
    assert isinstance(model["backend_url"], str) and model["backend_url"]
    assert isinstance(model["ollama_tag"], str) and model["ollama_tag"]


def test_get_model_unknown_returns_none():
    assert get_model("no-such-model") is None


def test_list_models_covers_the_seeded_names():
    names = list_models()
    assert isinstance(names, list)
    for name in SEEDED:
        assert name in names


def test_logical_models_table_matches_get_model():
    for name in SEEDED:
        assert LOGICAL_MODELS[name] == get_model(name)
