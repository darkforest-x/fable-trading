from yoyo.copier.tools.analyze_chartprime_history import parse_numbers


def test_parse_numbers_repairs_repeated_decimal_point():
    assert parse_numbers("1..04-1.09") == [1.04, 1.09]
