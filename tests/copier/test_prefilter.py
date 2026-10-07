import asyncio

import pytest

from yoyo.copier.ai.deepseek_client import DeepseekClient
from yoyo.copier.ai.prefilter import obviously_not_a_signal


@pytest.mark.parametrize("text", ["卧槽 怎么跌这么多", "@Crypto Signal", "注意了，马上触发价格。", "今天fomc 所以还是不要玩大", ""])
def test_chat_without_trading_content_is_filtered(text):
    assert obviously_not_a_signal(text)


@pytest.mark.parametrize("text", [
    "BTC不动", "兄弟们，再挂一单", "反馈盈利情况", "[图片消息] 图片", "全平", "止盈出局", "Closed in small profit",
    "TP1 hit", "$wif looks ready", "eth 78000-78300", "move sl to breakeven", "做空 等反弹",
])
def test_anything_that_might_be_a_trade_still_goes_to_the_model(text):
    assert not obviously_not_a_signal(text)


def test_filtered_message_never_reaches_the_api(monkeypatch):
    client = DeepseekClient()

    class Boom:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise AssertionError("API must not be called for filtered chat")

    client.client = Boom()
    result = asyncio.run(client.analyze("卧槽 怎么跌这么多"))
    assert result.intent == "noise" and "预过滤" in result.summary


def test_image_messages_are_never_prefiltered():
    client = DeepseekClient()
    client.client = None  # falls back to the local parser instead of the API
    result = asyncio.run(client.analyze("看图", image_text="BTC long 60000 sl 59000"))
    assert "预过滤" not in (result.summary or "")
