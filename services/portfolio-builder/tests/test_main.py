import talib


def test_ta_lib_is_importable():
    assert "ROCP" in talib.get_functions()
