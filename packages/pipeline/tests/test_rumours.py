from conftest import make_ctx
from dugout_pipeline.adapters.rumours import outlet_in_text, outlet_of, rumour_paragraphs
from dugout_pipeline.registry import build

BODY = (
    "<p>Monaco pulled out of a deal for <strong>Lamine Camara</strong> on deadline day.</p>"
    '<p> <span>Related: </span><a href="https://www.theguardian.com/x">All the deals</a> </p>'
    "<p>There are also whispers that Liverpool will make a move for Camara.</p>"
    "<p>Leeds are in the hat for <strong>Darío</strong> <strong>Osorio.</strong>, says "
    '<a href="https://www.thesun.co.uk/sport/1">the Sun</a>.</p>'
    "<p>A paragraph about nobody in particular.</p>"
)


def test_rumour_paragraphs_carry_players_and_credit_the_outlet():
    items = rumour_paragraphs(BODY)
    assert [i["players"] for i in items] == [["Lamine Camara"], ["Lamine Camara"], ["Darío Osorio"]]
    assert [i["outlet"] for i in items] == ["The Guardian", "The Guardian", "The Sun"]
    assert "Related" not in " ".join(i["text"] for i in items)


def test_outlet_of_skips_guardian_links_and_names_unknown_hosts():
    assert outlet_of('<a href="https://www.theguardian.com/a">x</a>') == "The Guardian"
    assert outlet_of('<a href="https://www.gazzetta.it/calcio/x">x</a>') == "La Gazzetta dello Sport"
    assert outlet_of('<a href="https://example-news.com/x">x</a>') == "example-news.com"
    assert outlet_of('<a href="https://www.youtube.com/watch?v=x">a song</a>') == "The Guardian"


def test_outlet_in_text_finds_the_first_named_outlet():
    assert outlet_in_text("Arsenal want Bob, says the Mirror. The Sun disagrees.") == "The Mirror"
    assert outlet_in_text("Sky Sports reports that ...") == "Sky Sports"
    assert outlet_in_text("AS Roma want him, and AS says so") == "AS"  # the club is not the paper
    assert outlet_in_text("Sunderland and Mail-order brides") is None  # whole words, case-sensitive
    assert outlet_in_text("the sun is out") is None


def test_unlinked_paragraph_credits_the_outlet_it_names():
    items = rumour_paragraphs("<p>Spurs want <strong>Bob Smith</strong>, reports the Daily Star.</p>")
    assert items[0]["outlet"] == "Daily Star"


def test_rumour_archive_replays_one_year(settings, reep):
    ctx = make_ctx(settings, reep, "rumour_archive")
    adapter = build("rumour_archive", ctx)
    assert adapter.ingest(years=[2016]) == {"rumours": 4}
    rows = ctx.con.execute(
        "SELECT rumour_id, players, outlet, paragraph FROM rumours ORDER BY published_at, paragraph"
    ).fetchall()
    assert rows[0][1] == ["Mario Gómez"]
    assert ["Charlie Austin", "Sandro", "Steven Caulker", "Lamine Koné"] in [r[1] for r in rows]
    assert all(r[0].endswith(f"-p{r[3]}") and r[2] == "The Guardian" for r in rows)
