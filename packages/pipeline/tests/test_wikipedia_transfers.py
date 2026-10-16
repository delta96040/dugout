from datetime import date

from dugout_pipeline.adapters.wikipedia_transfers import fx_lookup, parse_fee, parse_page

TABLE = """== Transfers ==
{| class="wikitable sortable"
!Date
!Player
!Moving from
!Moving to
!Fee
|-
| rowspan="2" |27 February 2026
|{{flagg|cxx|IRE}} {{sortname|Trent|Koné-Doherty}}
|[[Liverpool F.C.|Liverpool]]
|{{flagg|cxx|NOR}} [[Molde FK|Molde]]
|{{ntsh|0}}Free<ref>{{cite web|url=https://example.org|date=2026-02-27}}</ref>
|-
|{{flagg|cxx|USA}} {{sortname|Josh|Sargent}}
|[[Norwich City F.C.|Norwich City]]
|{{flagg|cxx|CAN}} [[Toronto FC]]
|{{ntsh|15500000}}£15.5m<ref name="x"/>
|-
|1 July 2026
|{{sortname|Jhon|Arias|dab=footballer}}
|[[Wolverhampton Wanderers F.C.|Wolverhampton Wanderers]]
|Free agent
|Released
|}
== Loans ==
{| class="wikitable sortable"
!Start date
!End date
!Name
!Moving from
!Moving to
|-
|{{dts|format=dmy|2026|8|1}}
|30 June 2027
|{{Sort|Nwaneri, Ethan|[[Ethan Nwaneri]]}}
|[[Arsenal F.C.|Arsenal]]
|[[Borussia Dortmund]]
|}
"""

BY_CLUB = """==Bundesliga==
===Bayern Munich===
'''In:'''
{{fs start|hidenote=yes}}
{{fs player|no=11|nat=GER|pos=DF|name=[[Nathaniel Brown (footballer)|Nathaniel Brown]]|other=from [[Eintracht Frankfurt]]}}<ref>{{cite web|title=x|date=3 July 2026}}</ref>
{{Fs end}}
'''Out:'''
{{fs start|hidenote=yes}}
{{fs player|no=8|nat=GER|pos=MF|name=[[Leon Goretzka]]|other=to [[Aston Villa F.C.|Aston Villa]]}}
{{fs player|no=|nat=GER|pos=MF|name=[[Arijon Ibrahimović]]|other=on loan to [[FC Augsburg]], previously on loan at [[1. FC Heidenheim]]}}
{{fs player|no=22|nat=POR|pos=DF|name=[[Raphaël Guerreiro]]|other=free agent}}
{{Fs end}}
===[[Eintracht Frankfurt]]===
'''In:'''
{{fs start|hidenote=yes}}
{{Fs end}}
'''Out:'''
{{fs start|hidenote=yes}}
{{fs player|no=|nat=GER|pos=DF|name=[[Nathaniel Brown (footballer)|Nathaniel Brown]]|other=to [[Bayern Munich]]}}
{{Fs end}}
"""


def test_table_layout_rows_rowspans_fees_and_loans():
    rows = {r["player_name"]: r for r in parse_page(TABLE, "English", "GBP")}
    assert set(rows) == {"Trent Koné-Doherty", "Josh Sargent", "Jhon Arias", "Ethan Nwaneri"}
    sargent = rows["Josh Sargent"]
    assert sargent["date"] == date(2026, 2, 27)  # carried down by the rowspan
    assert (sargent["fee_native"], sargent["currency"], sargent["kind"]) == (15_500_000, "GBP", "permanent")
    assert (sargent["from_title"], sargent["to_title"]) == ("Norwich City F.C.", "Toronto FC")
    assert rows["Trent Koné-Doherty"]["kind"] == "free"
    arias = rows["Jhon Arias"]
    assert arias["player_title"] == "Jhon Arias (footballer)"
    assert (arias["to_club"], arias["kind"]) == (None, "released")
    loan = rows["Ethan Nwaneri"]
    assert (loan["kind"], loan["date"], loan["loan_end"]) == ("loan", date(2026, 8, 1), date(2027, 6, 30))
    assert loan["to_title"] == "Borussia Dortmund"


def test_by_club_layout_directions_and_duplicates():
    rows = parse_page(BY_CLUB, "German", "EUR")
    brown = [r for r in rows if r["player_name"] == "Nathaniel Brown"]
    # listed under both clubs (In at Bayern, Out at Frankfurt): one row
    assert len(brown) == 1
    assert (brown[0]["from_title"], brown[0]["to_title"], brown[0]["kind"]) == (
        "Eintracht Frankfurt",
        "Bayern Munich",
        "permanent",
    )
    assert brown[0]["date"] == date(2026, 7, 3)  # the cited announcement
    by = {r["player_name"]: r for r in rows}
    assert by["Leon Goretzka"]["to_title"] == "Aston Villa F.C."
    assert (by["Arijon Ibrahimović"]["kind"], by["Arijon Ibrahimović"]["to_title"]) == ("loan", "FC Augsburg")
    assert (by["Raphaël Guerreiro"]["kind"], by["Raphaël Guerreiro"]["to_title"]) == ("released", None)


def test_fee_formats():
    assert parse_fee("€16,4M", "EUR")["fee_native"] == 16_400_000
    assert parse_fee("£3,000,000", "GBP")["fee_native"] == 3_000_000
    assert parse_fee("€700k", "EUR")["fee_native"] == 700_000
    assert parse_fee("Undisclosed", "EUR")["fee_native"] is None
    assert parse_fee("Loan", "GBP")["kind"] == "loan"


def test_fx_uses_the_last_rate_on_or_before_the_date():
    rates = {"GBP": [(date(2026, 7, 1), 0.85), (date(2026, 7, 3), 0.86)]}
    assert fx_lookup(rates, "GBP", date(2026, 7, 2)) == 0.85
    assert fx_lookup(rates, "GBP", None) == 0.86
    assert fx_lookup(rates, "EUR", None) == 1.0
