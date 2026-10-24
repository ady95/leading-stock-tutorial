"""11-3 거래비용 — 한 번 사고팔 때(왕복) 드는 비용을 수익률로.

    from ch11_backtest.costs import round_trip_cost
    cost = round_trip_cost(32_050)      # 0.0055... (0.55%)

수수료와 슬리피지는 가정값이다. 자기 증권사 수수료와 실제 체결 경험에 맞게 바꿔 쓴다.
"""

FEE = 0.00015        # 매수·매도 각각 수수료 0.015% (온라인 거래 가정. 증권사마다 다르다)
TAX = 0.0020         # 매도 시 증권거래세 0.20% (2026년, 코스피 거래세 0.05% + 농어촌특별세 0.15%, 코스닥 0.20%)
SLIP_TICKS = 1       # 매수·매도 각각 1호가 불리하게 체결된다고 가정 (사는 쪽은 매도호가, 파는 쪽은 매수호가)

# 호가 단위 (2023-01-25부터 유가증권시장·코스닥 같은 단위)
TICKS = [(2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)]


def tick_size(price):
    for limit, tick in TICKS:
        if price < limit:
            return tick
    return 1_000


def round_trip_cost(price, fee=FEE, tax=TAX, slip_ticks=SLIP_TICKS):
    """왕복 비용(수익률). 수수료 2번 + 거래세 1번 + 호가 차이 2번."""
    return 2 * fee + tax + 2 * slip_ticks * tick_size(price) / price


if __name__ == "__main__":
    for p in (1_500, 4_800, 9_990, 32_050, 71_200, 210_000):
        print(f"{p:>9,}원: 호가 단위 {tick_size(p):>5,}원, 왕복 비용 {round_trip_cost(p):.3%}")
